from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

_LOG_FILE = Path("/share/fleet_command.log")


def _flog(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

from app.jobs import (
    load_job, save_job, append_log,
    write_stage_output, read_stage_output,
    is_cancelled, rerun_from_stage,
    STATUS_RUNNING, STATUS_DONE, STATUS_FAILED,
)
from app.roles import load_roles, ROLE_META
from app.harnesses import get_harness

# ── HA card reference baked in — enough for generator to produce valid YAML ──

HA_REFERENCE = """
YAML only. No fences. No explanations.
Structure:
title: "Title"
views:
  - title: "View"
    cards:
      - type: TYPE
        entity: domain.name
Cards: sensor, entities, gauge, weather-forecast, history-graph, button, markdown, grid, vertical-stack
Rules: every card needs type. entities card uses list under entities key.
"""


def _build_payload(harness: dict[str, Any], system: str, user: str) -> dict[str, Any]:
    model = harness.get("model", "")
    fmt = harness.get("request_format", "ollama_chat")
    params = harness.get("params", {})
    temp = params.get("temperature", 0)

    if fmt == "ollama_chat":
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": temp, "num_predict": 1024},
        }
    if fmt == "ollama_generate":
        return {"model": model, "prompt": f"{system}\n\n{user}", "stream": False}
    if fmt == "openai_chat":
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temp,
        }
    if fmt == "anthropic_messages":
        return {
            "model": model,
            "system": system,
            "max_tokens": params.get("max_tokens", 4096),
            "messages": [{"role": "user", "content": user}],
        }
    return {"model": model, "prompt": f"{system}\n\n{user}"}


def _extract(response: httpx.Response, fmt: str) -> str:
    try:
        data = response.json()
        if fmt == "ollama_chat":
            return data.get("message", {}).get("content", response.text)
        if fmt == "ollama_generate":
            return data.get("response", response.text)
        if fmt in ("openai_chat",):
            choices = data.get("choices", [])
            return choices[0].get("message", {}).get("content", response.text) if choices else response.text
        if fmt == "anthropic_messages":
            content = data.get("content", [])
            return content[0].get("text", response.text) if content else response.text
    except Exception:
        pass
    return response.text


def _auth_headers(harness: dict[str, Any]) -> dict[str, str]:
    from app.workers import configured_workers, worker_secret
    auth = harness.get("auth_type", "none")
    if auth == "none":
        return {}
    model = harness.get("model", "")
    for w in configured_workers():
        if w.get("model") == model:
            secret = worker_secret(int(w["id"]))
            if secret:
                if auth == "bearer":
                    return {"Authorization": f"Bearer {secret}"}
                if auth == "x_api_key":
                    return {"x-api-key": secret}
                header = harness.get("auth_header") or w.get("auth_header") or "Authorization"
                return {header: secret}
    return {}


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```ya?ml\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _ollama_host() -> str:
    from app.config import options
    host = str(options().get("ollama_host", "") or "").strip().rstrip("/")
    if host and not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host


def _resolve_endpoint(harness: dict[str, Any]) -> str:
    from app.workers import ensure_path
    fmt = harness.get("request_format", "")
    api_path = ensure_path(harness.get("api_path") or "")

    # Local Ollama harnesses — always use the global ollama_host setting
    if fmt in ("ollama_chat", "ollama_generate"):
        host = _ollama_host()
        if host:
            endpoint = host + api_path
            _flog(f"  using ollama_host: {endpoint}")
            return endpoint

    # Cloud/API harnesses — use harness endpoint as-is
    return harness.get("endpoint", "").rstrip("/") + api_path


async def _call_harness(harness: dict[str, Any], system: str, user: str) -> str:
    endpoint = _resolve_endpoint(harness)
    payload = _build_payload(harness, system, user)
    auth_headers = _auth_headers(harness)
    headers = {"Content-Type": "application/json", **auth_headers}
    fmt = harness.get("request_format", "ollama_chat")

    _flog(f"CALL model={harness.get('model')} fmt={fmt}")
    _flog(f"  endpoint={endpoint}")
    _flog(f"  auth={'yes ('+harness.get('auth_type')+')' if auth_headers else 'none'}")
    _flog(f"  payload_keys={list(payload.keys())}")
    if "messages" in payload:
        _flog(f"  messages={len(payload['messages'])} total_chars={sum(len(m.get('content','')) for m in payload['messages'])}")

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
            elapsed = time.monotonic() - t0
            _flog(f"  response status={resp.status_code} elapsed={elapsed:.1f}s size={len(resp.content)}b")
            resp.raise_for_status()
            result = _extract(resp, fmt)
            _flog(f"  extracted={len(result)} chars preview={result[:120].strip()!r}")
            return result
    except Exception as exc:
        elapsed = time.monotonic() - t0
        _flog(f"  ERROR after {elapsed:.1f}s: {exc}")
        raise


# Escalation order — when a worker is unavailable, try the next rank up
_FALLBACK_ORDER = ["generator", "reviewer", "supervisor", "advisor"]


async def _call_with_fallback(
    stage: str,
    primary_harness: dict[str, Any],
    system: str,
    user: str,
    roles: dict[str, Any],
    job: dict[str, Any],
) -> tuple[str, str]:
    """Try primary harness, escalate up the chain, then wrap to highest available. Returns (raw_output, actual_role_used)."""
    primary_id = primary_harness.get("id") or primary_harness.get("harness_id")
    tried_ids: set[str] = set()

    try:
        return await _call_harness(primary_harness, system, user), stage
    except Exception as primary_exc:
        _flog(f"  ESCALATE: {stage} primary failed — walking up chain")
        tried_ids.add(primary_id or "")

    start_idx = _FALLBACK_ORDER.index(stage) + 1 if stage in _FALLBACK_ORDER else len(_FALLBACK_ORDER)

    # Walk up from current role toward the top
    candidates = _FALLBACK_ORDER[start_idx:] + _FALLBACK_ORDER[:start_idx]

    for fallback_role in candidates:
        if fallback_role == stage:
            continue
        assignment = roles.get(fallback_role, {})
        fb_harness_id = assignment.get("harness_id")
        if not fb_harness_id:
            continue
        fb_harness = get_harness(fb_harness_id)
        if not fb_harness:
            continue
        fb_id = fb_harness.get("id") or fb_harness_id
        if fb_id in tried_ids:
            continue

        tried_ids.add(fb_id)
        append_log(job, stage, f"Escalating to {fb_harness.get('display_name', fallback_role)} ({fallback_role})")
        _flog(f"  ESCALATE → {fallback_role} ({fb_harness.get('model')})")
        try:
            return await _call_harness(fb_harness, system, user), fallback_role
        except Exception as fb_exc:
            _flog(f"  ESCALATE {fallback_role} also failed: {fb_exc}")
            continue

    raise RuntimeError(f"All workers exhausted for stage '{stage}' — no available harness responded")


def _count_rejection_issues(text: str) -> int:
    return len(re.findall(r'^\s*\d+[\.\)]\s+', text, re.MULTILINE))


def _user_prompt(stage: str, spec: str, prev: str | None, task: str | None = None, block: str | None = None, extra: str | None = None, **_kwargs: Any) -> str:
    from app.pipeline_prompts import render_prompt
    if stage == "generator" and task:
        prompt = render_prompt("generator", spec=spec, task=task, block=block or "", prev=prev or "")
        prompt = f"{HA_REFERENCE}\n{prompt}"
    elif stage == "generator":
        prompt = render_prompt("generator_single", spec=spec, prev=prev or "")
        prompt = f"{HA_REFERENCE}\n{prompt}"
    elif stage == "manager":
        prompt = render_prompt("manager", spec=spec, prev=(prev or "")[:600])
    else:
        prompt = render_prompt(stage, spec=spec, prev=prev or "")
    if extra:
        prompt += f"\n\nAdditional instructions: {extra}"
    return prompt


def _parse_blocks_and_tasks(text: str) -> list[dict[str, str]]:
    """Parse manager output into [{block, task}] list. Tolerates markdown formatting."""
    tasks = []
    current_block = "Main"
    for raw_line in text.splitlines():
        # Strip markdown bold/headers/bullets before matching
        line = re.sub(r'[*_#`]+', '', raw_line).strip()
        if not line:
            continue

        block_match = re.match(r'^BLOCK\s*\d*\s*:?\s*(.+)', line, re.IGNORECASE)
        if block_match:
            current_block = block_match.group(1).strip().rstrip(':').strip()
            _flog(f"  parse block: {current_block!r}")
            continue

        task_match = re.match(r'^-?\s*Task\s*\d+\s*:?\s*(.+)', line, re.IGNORECASE)
        if not task_match:
            task_match = re.match(r'^[-•]\s+(.+)', raw_line.strip())
        if not task_match:
            task_match = re.match(r'^\d+[\.\)]\s+(.+)', line)

        if task_match:
            task_text = task_match.group(1).strip()
            if task_text and not re.match(r'^BLOCK', task_text, re.IGNORECASE):
                tasks.append({"block": current_block, "task": task_text})

    _flog(f"  parse result: {len(tasks)} tasks")
    return tasks


def _assemble_yaml_fragments(fragments: list[dict[str, str]]) -> str:
    """
    Assemble card fragments grouped by block into a complete Lovelace YAML.
    Each fragment dict has keys: block, task, yaml.
    """
    import yaml as _yaml

    blocks: dict[str, list[str]] = {}
    for f in fragments:
        blocks.setdefault(f["block"], []).append(f["yaml"])

    views = []
    for block_name, card_yamls in blocks.items():
        cards = []
        for raw in card_yamls:
            try:
                parsed = _yaml.safe_load(raw)
                if isinstance(parsed, dict):
                    cards.append(parsed)
                elif isinstance(parsed, list):
                    cards.extend(parsed)
            except Exception:
                # Keep raw string as markdown fallback
                cards.append({"type": "markdown", "content": raw[:200]})
        views.append({"title": block_name, "cards": cards})

    dashboard = {"title": "Fleet Output", "views": views}
    return _yaml.dump(dashboard, default_flow_style=False, allow_unicode=True, sort_keys=False)


async def run_stage(job_id: str, stage: str) -> dict[str, Any]:
    job = load_job(job_id)
    if not job:
        return {"ok": False, "error": "job not found"}

    roles = load_roles()
    assignment = roles.get(stage, {})
    harness_id = assignment.get("harness_id")
    harness = get_harness(harness_id) if harness_id else None

    if not harness:
        msg = f"No model assigned to role: {stage}"
        append_log(job, stage, f"SKIP — {msg}")
        save_job(job)
        return {"ok": False, "error": msg}

    full_persona = ROLE_META.get(stage, {}).get("persona", "You are a helpful AI assistant.")
    # Trim persona for small models — keep first sentence only
    persona = full_persona.split(".")[0] + "." if full_persona else "You are a helpful AI assistant."
    spec = job.get("spec", "")
    extra = job.get("stage_instructions", {}).get(stage)
    rejection_feedback = job.get("rejection_feedback") if stage in ("project_manager", "manager", "generator") else None
    if rejection_feedback:
        extra = (extra or "") + f"\n\nPrevious attempt was rejected. Feedback:\n{rejection_feedback}"

    # Each stage reads from its predecessor
    prev_stages = {
        "manager":   "project_manager",
        "generator": "manager",
        "assembler": "generator",
        "reviewer":  "assembler",
        "supervisor": "reviewer",
    }
    prev = read_stage_output(job_id, prev_stages.get(stage, "")) if stage in prev_stages else None

    user = _user_prompt(stage, spec, prev, extra=extra)

    append_log(job, stage, f"Calling {harness.get('display_name', harness_id)}...")
    job["stages"][stage] = {"status": "running"}
    save_job(job)

    try:
        raw, handled_by = await _call_with_fallback(stage, harness, persona, user, roles, job)
        output = _strip_fences(raw)

        # Reviewer escalation: count issues, fix inline if within threshold
        if stage == "reviewer" and output.strip().upper().startswith("REJECTED"):
            from app.pipeline_rules import reviewer_threshold
            issue_count = _count_rejection_issues(output)
            threshold = reviewer_threshold()
            if issue_count <= threshold:
                append_log(job, stage, f"Found {issue_count} issues (≤{threshold}) — fixing inline")
                fix_user = (
                    f"Issues found:\n{output}\n\n"
                    f"Original YAML:\n{prev}\n\n"
                    f"Fix ALL listed issues. Output corrected YAML only. No explanations."
                )
                try:
                    raw2, handled_by = await _call_with_fallback(stage, harness, persona, fix_user, roles, job)
                    output = _strip_fences(raw2)
                except Exception:
                    pass  # Keep rejection if fix attempt fails

        write_stage_output(job_id, stage, output)
        note = f" (handled by {handled_by})" if handled_by != stage else ""
        job["stages"][stage] = {"status": "done", "preview": output[:400], "handled_by": handled_by}
        append_log(job, stage, f"Done — {len(output)} chars{note}")
        save_job(job)
        return {"ok": True, "stage": stage, "output": output, "handled_by": handled_by}

    except Exception as exc:
        job["stages"][stage] = {"status": "error", "error": str(exc)}
        append_log(job, stage, f"ERROR — all workers exhausted: {exc}")
        save_job(job)
        return {"ok": False, "error": str(exc)}


async def _call_assembler(job_id: str, fragments: list[dict[str, str]], roles: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    from app.pipeline_prompts import render_prompt
    assignment = roles.get("assembler", {})
    harness_id = assignment.get("harness_id")
    harness = get_harness(harness_id) if harness_id else None

    spec = job.get("spec", "")
    fragments_text = "\n".join(
        f"--- Fragment {i+1} (Block: {f['block']}, Task: {f['task']}) ---\n{f['yaml']}"
        for i, f in enumerate(fragments)
    )

    if not harness:
        _flog("  assembler: no harness assigned, using Python fallback")
        append_log(job, "assembler", "No assembler assigned — using Python assembly fallback")
        result = _assemble_yaml_fragments(fragments)
        write_stage_output(job_id, "assembler", result)
        job["stages"]["assembler"] = {"status": "done", "preview": result[:400], "handled_by": "python"}
        save_job(job)
        return {"ok": True, "output": result}

    persona = ROLE_META.get("assembler", {}).get("persona", "You are an integration engineer.")
    persona = persona.split(".")[0] + "."
    user = render_prompt("assembler", spec=spec, fragments=fragments_text)

    append_log(job, "assembler", f"Assembling {len(fragments)} fragments with {harness.get('display_name', harness_id)}...")
    job["stages"]["assembler"] = {"status": "running"}
    save_job(job)

    try:
        raw, handled_by = await _call_with_fallback("assembler", harness, persona, user, roles, job)
        output = _strip_fences(raw)
        write_stage_output(job_id, "assembler", output)
        job["stages"]["assembler"] = {"status": "done", "preview": output[:400], "handled_by": handled_by}
        append_log(job, "assembler", f"Done — {len(output)} chars")
        save_job(job)
        return {"ok": True, "output": output}
    except Exception as exc:
        job["stages"]["assembler"] = {"status": "error", "error": str(exc)}
        append_log(job, "assembler", f"ERROR: {exc}")
        save_job(job)
        return {"ok": False, "error": str(exc)}


async def _run_generator_loop(job_id: str, roles: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """Run generator once per task from manager's block/task breakdown, then assemble."""
    manager_output = read_stage_output(job_id, "manager")
    task_list = _parse_blocks_and_tasks(manager_output) if manager_output else []

    if len(task_list) < 2:
        # Manager didn't produce a task list — run generator with spec only, not the full manager dump
        _flog(f"  generator: no task list found, falling back to spec-only run")
        append_log(job, "generator", "WARNING: no task list parsed from manager output — running single pass from spec")
        spec = job.get("spec", "")
        assignment = roles.get("generator", {})
        harness_id = assignment.get("harness_id")
        harness = get_harness(harness_id) if harness_id else None
        if not harness:
            return {"ok": False, "error": "No model assigned to role: generator"}
        persona = ROLE_META.get("generator", {}).get("persona", "You are a helpful AI assistant.")
        persona = persona.split(".")[0] + "."
        user = _user_prompt("generator", spec, None)
        append_log(job, "generator", "No task list — running single pass from spec")
        job["stages"]["generator"] = {"status": "running"}
        save_job(job)
        try:
            raw, handled_by = await _call_with_fallback("generator", harness, persona, user, roles, job)
            output = _strip_fences(raw)
            write_stage_output(job_id, "generator", output)
            job["stages"]["generator"] = {"status": "done", "preview": output[:400], "handled_by": handled_by}
            append_log(job, "generator", f"Done — {len(output)} chars")
            save_job(job)
            return {"ok": True, "stage": "generator", "output": output, "handled_by": handled_by}
        except Exception as exc:
            job["stages"]["generator"] = {"status": "error", "error": str(exc)}
            save_job(job)
            return {"ok": False, "error": str(exc)}

    assignment = roles.get("generator", {})
    harness_id = assignment.get("harness_id")
    harness = get_harness(harness_id) if harness_id else None
    if not harness:
        return {"ok": False, "error": "No model assigned to role: generator"}

    persona = ROLE_META.get("generator", {}).get("persona", "You are a helpful AI assistant.")
    persona = persona.split(".")[0] + "."
    spec = job.get("spec", "")

    block_count = len(set(t['block'] for t in task_list))
    _flog(f"  generator loop: {len(task_list)} tasks across {block_count} blocks")
    append_log(job, "generator", f"Starting loop: {len(task_list)} tasks, {block_count} blocks")

    fragments: list[dict[str, str]] = []

    for i, item in enumerate(task_list):
        if is_cancelled(job_id):
            return {"ok": False, "error": "cancelled"}

        label = f"Task {i+1}/{len(task_list)} [{item['block']}]: {item['task'][:50]}"
        append_log(job, "generator", label)
        job["stages"]["generator"] = {"status": "running", "progress": f"{i+1}/{len(task_list)}"}
        save_job(job)

        user = _user_prompt("generator", spec, None, task=item["task"], block=item["block"])
        try:
            raw, _ = await _call_with_fallback("generator", harness, persona, user, roles, job)
            fragment = _strip_fences(raw)
            fragments.append({"block": item["block"], "task": item["task"], "yaml": fragment})
            _flog(f"  task {i+1} done: {len(fragment)} chars")
        except Exception as exc:
            _flog(f"  task {i+1} failed: {exc}")
            job["stages"]["generator"] = {"status": "error", "error": str(exc)}
            append_log(job, "generator", f"ERROR on task {i+1}: {exc}")
            save_job(job)
            return {"ok": False, "error": str(exc)}

    # Mark generator done, store raw fragments
    frags_raw = "\n\n".join(f"# Block: {f['block']}\n{f['yaml']}" for f in fragments)
    write_stage_output(job_id, "generator", frags_raw)
    job["stages"]["generator"] = {"status": "done", "preview": frags_raw[:400], "handled_by": "generator", "tasks_completed": len(fragments)}
    append_log(job, "generator", f"Done — {len(fragments)} fragments collected")
    save_job(job)

    # Assembler: capable model combines fragments into complete output
    assemble_result = await _call_assembler(job_id, fragments, roles, job)
    if not assemble_result["ok"]:
        return assemble_result

    final = assemble_result["output"]
    return {"ok": True, "stage": "generator", "output": final, "handled_by": "generator"}


async def run_pipeline(job_id: str) -> None:
    job = load_job(job_id)
    if not job:
        return

    job["status"] = STATUS_RUNNING
    save_job(job)
    _flog(f"=== JOB {job_id} START pipeline={job.get('pipeline')} ===")

    roles = load_roles()
    pipeline = job.get("pipeline", ["generator"])
    prev_output: str | None = None

    for stage in pipeline:
        if is_cancelled(job_id):
            job = load_job(job_id)
            job["status"] = "cancelled"
            save_job(job)
            return

        if stage == "generator" and "manager" in pipeline:
            result = await _run_generator_loop(job_id, roles, job)
        else:
            result = await run_stage(job_id, stage)
        if not result["ok"]:
            job = load_job(job_id)
            job["status"] = STATUS_FAILED
            save_job(job)
            return
        prev_output = result.get("output")
        job = load_job(job_id)

        # Supervisor rejection — check for REJECTED_AT routing
        if stage == "supervisor" and prev_output and prev_output.strip().upper().startswith("REJECTED"):
            match = re.search(r'REJECTED_AT:\s*(\w+)', prev_output, re.IGNORECASE)
            target = match.group(1).strip() if match else None
            if target and target in pipeline:
                job = load_job(job_id)
                job["rejection_feedback"] = prev_output
                save_job(job)
                rerun_from_stage(job_id, target)
                _flog(f"  REJECTED_AT={target} — re-running from {target}")
                start_idx = pipeline.index(target)
                for rerun_stage in pipeline[start_idx:]:
                    if is_cancelled(job_id):
                        return
                    if rerun_stage == "generator" and "manager" in pipeline:
                        r = await _run_generator_loop(job_id, roles, load_job(job_id))
                    else:
                        r = await run_stage(job_id, rerun_stage)
                    if not r["ok"]:
                        job = load_job(job_id)
                        job["status"] = STATUS_FAILED
                        save_job(job)
                        return
                    job = load_job(job_id)
                # Keep rejection_feedback in job so manual reruns can still use it
                prev_output = r.get("output")
            else:
                job = load_job(job_id)
                job["rejection_feedback"] = prev_output
                append_log(job, stage, "Rejected — use ↺ Retry to rerun with adjusted settings")
                job["status"] = STATUS_FAILED
                save_job(job)
                return

    # All stages passed
    if prev_output:
        write_stage_output(job_id, "final", prev_output)
        job = load_job(job_id)
        job["final_output"] = prev_output[:600]

    job["status"] = STATUS_DONE
    append_log(job, "pipeline", "All stages complete")
    save_job(job)

    if job.get("type") == "ha_dashboard" and prev_output and not prev_output.strip().upper().startswith("REJECTED"):
        await _push_dashboard(job, prev_output)


async def _push_dashboard(job: dict[str, Any], yaml_content: str) -> None:
    import os
    import yaml as _yaml

    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        append_log(job, "ha_push", "No SUPERVISOR_TOKEN — output saved to run file only")
        save_job(job)
        return

    try:
        dashboard_id = job.get("target_dashboard", "fleet_output")

        # Try to parse to confirm it's valid YAML before pushing
        _yaml.safe_load(yaml_content)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"http://supervisor/core/api/lovelace/dashboards/{dashboard_id}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"mode": "yaml"},
            )
            # Dashboard may already exist — either way attempt config update
            resp2 = await client.post(
                f"http://supervisor/core/api/lovelace/dashboards/{dashboard_id}/config",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"config": yaml_content},
            )
            ok = resp2.is_success or resp.is_success
            append_log(job, "ha_push", f"Dashboard push {'OK' if ok else 'FAILED'} — {resp2.status_code}")

    except Exception as exc:
        append_log(job, "ha_push", f"HA push error — {exc}")

    save_job(job)
