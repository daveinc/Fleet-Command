from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# Semaphore sized to number of enabled workers — computed lazily on first pipeline run
_PIPELINE_SEM: asyncio.Semaphore | None = None

def _get_pipeline_sem() -> asyncio.Semaphore:
    global _PIPELINE_SEM
    if _PIPELINE_SEM is None:
        from app.config import options
        opts = options()
        enabled = sum(1 for i in range(1, 5) if opts.get(f"worker_{i}_enabled", False))
        _PIPELINE_SEM = asyncio.Semaphore(max(1, enabled))
    return _PIPELINE_SEM

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
    write_stage_input, read_stage_input,
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


def _estimate_input_tokens(system: str, user: str) -> int:
    """Rough estimate: 1 token ≈ 4 chars."""
    return (len(system) + len(user)) // 4


def _safe_output_budget(harness: dict[str, Any], system: str, user: str, default: int = 1024) -> int:
    """Return max output tokens that fits within the harness context window."""
    ctx = harness.get("context_window")
    if not ctx:
        return default
    estimated_input = _estimate_input_tokens(system, user)
    # Leave 10% headroom for safety
    remaining = int(ctx * 0.9) - estimated_input
    return max(256, min(remaining, default))


def _build_payload(harness: dict[str, Any], system: str, user: str) -> dict[str, Any]:
    model = harness.get("model", "")
    fmt = harness.get("request_format", "ollama_chat")
    params = harness.get("params", {})
    temp = params.get("temperature", 0)
    out_budget = _safe_output_budget(harness, system, user)

    if fmt == "ollama_chat":
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": temp, "num_predict": out_budget},
        }
    if fmt == "ollama_generate":
        return {"model": model, "prompt": f"{system}\n\n{user}", "stream": False,
                "options": {"num_predict": out_budget}}
    if fmt == "openai_chat":
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temp,
            "max_tokens": out_budget,
        }
    if fmt == "anthropic_messages":
        explicit = params.get("max_tokens", 4096)
        return {
            "model": model,
            "system": system,
            "max_tokens": min(explicit, out_budget) if harness.get("context_window") else explicit,
            "messages": [{"role": "user", "content": user}],
        }
    return {"model": model, "prompt": f"{system}\n\n{user}"}


def _extract(response: httpx.Response, fmt: str) -> tuple[str, dict[str, int]]:
    """Returns (text, {"input": n, "output": n})."""
    tok: dict[str, int] = {"input": 0, "output": 0}
    try:
        data = response.json()
        if fmt == "ollama_chat":
            tok["input"] = data.get("prompt_eval_count", 0)
            tok["output"] = data.get("eval_count", 0)
            return data.get("message", {}).get("content", response.text), tok
        if fmt == "ollama_generate":
            tok["input"] = data.get("prompt_eval_count", 0)
            tok["output"] = data.get("eval_count", 0)
            return data.get("response", response.text), tok
        if fmt == "openai_chat":
            usage = data.get("usage", {})
            tok["input"] = usage.get("prompt_tokens", 0)
            tok["output"] = usage.get("completion_tokens", 0)
            choices = data.get("choices", [])
            return (choices[0].get("message", {}).get("content", response.text) if choices else response.text), tok
        if fmt == "openai_responses":
            usage = data.get("usage", {})
            tok["input"] = usage.get("input_tokens", 0)
            tok["output"] = usage.get("output_tokens", 0)
            output = data.get("output", [])
            text = next((b.get("text","") for item in output for b in item.get("content",[]) if b.get("type")=="text"), response.text)
            return text, tok
        if fmt == "anthropic_messages":
            usage = data.get("usage", {})
            tok["input"] = usage.get("input_tokens", 0)
            tok["output"] = usage.get("output_tokens", 0)
            content = data.get("content", [])
            return (content[0].get("text", response.text) if content else response.text), tok
    except Exception:
        pass
    return response.text, tok


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
    """Strip outer markdown code fences."""
    text = text.strip()
    text = re.sub(r"^```ya?ml\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _strip_all_fences(text: str) -> str:
    """Remove every markdown fence line from anywhere in the text."""
    lines = [l for l in text.splitlines() if not re.match(r"^\s*```", l)]
    return "\n".join(lines).strip()


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


async def _call_harness(
    harness: dict[str, Any],
    system: str,
    user: str,
    job: dict[str, Any] | None = None,
    stage: str = "",
) -> tuple[str, dict[str, int]]:
    """Returns (text, token_counts). Raises RuntimeError if input overflows context window."""
    from app.message_builder import overflow_info

    ctx = harness.get("context_window")
    info = overflow_info(system, user, harness)

    if info["overflows"]:
        msg = (
            f"Input overflow: ~{info['estimated']} tokens estimated "
            f"vs {info['ctx_window']} ctx window ({info['pct']}% used) "
            f"on {harness.get('display_name', harness.get('model', '?'))}"
        )
        _flog(f"  OVERFLOW: {msg}")
        if job and stage:
            append_log(job, stage, f"⚠ {msg}")
        raise RuntimeError(msg)

    if ctx and info["pct"] >= 70:
        _flog(f"  ctx usage ~{info['pct']}% before call — approaching limit")

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
            result, tokens = _extract(resp, fmt)
            _flog(f"  extracted={len(result)} chars tokens=in:{tokens['input']} out:{tokens['output']} preview={result[:120].strip()!r}")
            # Post-call: warn if actual input usage is high
            if ctx and tokens["input"] > 0:
                actual_pct = int(tokens["input"] / ctx * 100)
                if actual_pct >= 80:
                    warn = f"High ctx usage after call: {tokens['input']}/{ctx} tokens ({actual_pct}%)"
                    _flog(f"  WARNING: {warn}")
                    if job and stage:
                        append_log(job, stage, f"⚠ {warn}")
            return result, tokens
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
) -> tuple[str, str, dict[str, int]]:
    """Try primary harness, escalate up the chain. Returns (raw_output, actual_role_used, token_counts).
    On input overflow, prefers escalating to a harness with a larger context window before giving up."""
    from app.message_builder import overflow_info

    # Use harness_id strings as identity — harness dicts have no "id" field
    primary_harness_id = roles.get(stage, {}).get("harness_id", "") or ""
    tried_harness_ids: set[str] = set()

    primary_info = overflow_info(system, user, primary_harness)
    primary_overflows = primary_info["overflows"]

    if not primary_overflows:
        try:
            text, tokens = await _call_harness(primary_harness, system, user, job, stage)
            return text, stage, tokens
        except RuntimeError as e:
            if "overflow" in str(e).lower():
                primary_overflows = True
            else:
                _flog(f"  ESCALATE: {stage} primary failed — walking up chain")
        except Exception:
            _flog(f"  ESCALATE: {stage} primary failed — walking up chain")
    else:
        _flog(f"  OVERFLOW detected before call — skipping primary, seeking larger ctx window")
        append_log(job, stage, f"⚠ Input too large for primary worker (~{primary_info['pct']}% of {primary_info['ctx_window']} tokens) — routing up")

    tried_harness_ids.add(primary_harness_id)

    # Build candidate list — on overflow, sort by context window descending to find best fit
    start_idx = _FALLBACK_ORDER.index(stage) + 1 if stage in _FALLBACK_ORDER else len(_FALLBACK_ORDER)
    candidates = _FALLBACK_ORDER[start_idx:] + _FALLBACK_ORDER[:start_idx]

    fallback_harnesses: list[tuple[str, str, dict[str, Any]]] = []
    for fallback_role in candidates:
        if fallback_role == stage:
            continue
        assignment = roles.get(fallback_role, {})
        fb_harness_id = assignment.get("harness_id", "")
        if not fb_harness_id or fb_harness_id in tried_harness_ids:
            continue
        fb_harness = get_harness(fb_harness_id)
        if not fb_harness:
            continue
        fallback_harnesses.append((fallback_role, fb_harness_id, fb_harness))

    if primary_overflows:
        fallback_harnesses.sort(
            key=lambda x: x[2].get("context_window") or 0,
            reverse=True,
        )

    for fallback_role, fb_harness_id, fb_harness in fallback_harnesses:
        if fb_harness_id in tried_harness_ids:
            continue
        tried_harness_ids.add(fb_harness_id)

        fb_ctx = fb_harness.get("context_window")
        fb_info = overflow_info(system, user, fb_harness)
        if fb_info["overflows"]:
            _flog(f"  SKIP {fallback_role}/{fb_harness_id} — would also overflow ({fb_info['pct']}%)")
            continue

        append_log(job, stage, f"Escalating to {fb_harness.get('display_name', fallback_role)} ({fallback_role})" +
                   (f" — larger ctx {fb_ctx}" if primary_overflows and fb_ctx else ""))
        _flog(f"  ESCALATE → {fallback_role}/{fb_harness_id} ({fb_harness.get('model')})")
        try:
            text, tokens = await _call_harness(fb_harness, system, user, job, stage)
            return text, fallback_role, tokens
        except Exception as fb_exc:
            _flog(f"  ESCALATE {fallback_role} also failed: {fb_exc}")
            continue

    raise RuntimeError(f"All workers exhausted for stage '{stage}' — no available harness responded")


def _accum_tokens(job: dict[str, Any], stage: str, tokens: dict[str, int]) -> None:
    """Add token counts to stage entry and job total."""
    s = job.setdefault("stages", {}).setdefault(stage, {})
    st = s.setdefault("tokens", {"input": 0, "output": 0})
    st["input"] += tokens.get("input", 0)
    st["output"] += tokens.get("output", 0)
    jt = job.setdefault("tokens_total", {"input": 0, "output": 0})
    jt["input"] += tokens.get("input", 0)
    jt["output"] += tokens.get("output", 0)


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
        prompt = render_prompt("manager", spec=spec, prev=(prev or "")[:800])
        prompt += "\n\nHard limit: maximum 12 tasks total across all blocks. If the spec requires more, group similar items into one task."
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


_REVIEW_CHUNK_THRESHOLD = 1200  # chars — above this, review view-by-view


def _split_yaml_views(yaml_text: str) -> list[tuple[str, str]]:
    """Split assembled dashboard YAML into (view_title, view_block) pairs."""
    views = []
    current_title = "View"
    current_lines: list[str] = []
    in_views = False
    for line in yaml_text.splitlines():
        if line.strip().startswith("views:"):
            in_views = True
            continue
        if not in_views:
            continue
        # Detect new view entry
        m = re.match(r'\s{2}-\s+title:\s*(.+)', line)
        if m:
            if current_lines:
                views.append((current_title, "\n".join(current_lines)))
            current_title = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        views.append((current_title, "\n".join(current_lines)))
    return views


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
        extra = (extra or "") + (
            f"\n\nThis is a retry. The original request was:\n{spec}\n\n"
            f"The previous attempt was rejected with these remarks:\n{rejection_feedback}\n\n"
            f"Address all rejection remarks in your output."
        )

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
    write_stage_input(job_id, stage, user)

    append_log(job, stage, f"Calling {harness.get('display_name', harness_id)}...")
    job["stages"][stage] = {"status": "running"}
    save_job(job)

    try:
        # Reviewer: if input is large, review view-by-view to stay within context limits
        if stage == "reviewer" and prev and len(prev) > _REVIEW_CHUNK_THRESHOLD:
            view_pairs = _split_yaml_views(prev)
            if len(view_pairs) > 1:
                append_log(job, stage, f"Large output ({len(prev)} chars) — reviewing {len(view_pairs)} views separately")
                reviewed_views: list[str] = []
                all_notes: list[str] = []
                for view_title, view_block in view_pairs:
                    view_prompt = (
                        f"Spec: {spec}\n\n"
                        f"Review this Lovelace view section (title: {view_title}):\n{view_block}\n\n"
                        "Check: valid card types, markdown cards use 'content:' not 'entity:', no sensor definitions.\n"
                        "First line: '# REVIEW: <verdict>'\n"
                        "Then output the corrected or unchanged cards YAML. YAML only after the review line."
                    )
                    raw_v, handled_by, tok_v = await _call_with_fallback(stage, harness, persona, view_prompt, roles, job)
                    _accum_tokens(job, stage, tok_v)
                    stripped_v = _strip_all_fences(_strip_fences(raw_v))
                    lines_v = stripped_v.splitlines()
                    note_lines = [l for l in lines_v if l.strip().startswith("#")]
                    yaml_lines = [l for l in lines_v if not l.strip().startswith("#")]
                    if note_lines:
                        all_notes.append(f"{view_title}: " + " ".join(l.replace("#","").strip() for l in note_lines))
                    cards = "\n".join(yaml_lines).strip() or view_block
                    reviewed_views.append(f"  - title: {view_title}\n" + "\n".join(f"    {l}" for l in cards.splitlines()))
                dashboard_title = prev.splitlines()[0].replace("title:", "").strip() if prev.startswith("title:") else "Dashboard"
                output = f"title: {dashboard_title}\nviews:\n" + "\n".join(reviewed_views)
                review_notes = "; ".join(all_notes) if all_notes else None
                write_stage_input(job_id, stage, f"[view-by-view review of {len(view_pairs)} views]\n" + user)
                write_stage_output(job_id, stage, output)
                note = f" (handled by {handled_by})" if handled_by != stage else ""
                stage_data: dict = {
                    "status": "done", "preview": output[:400], "handled_by": handled_by,
                    "model_name": harness.get("display_name", harness_id),
                    "ctx_window": harness.get("context_window"),
                }
                if job.get("stages", {}).get(stage, {}).get("tokens"):
                    stage_data["tokens"] = job["stages"][stage]["tokens"]
                if review_notes:
                    stage_data["review_notes"] = review_notes
                    append_log(job, stage, f"Review: {review_notes[:300]}")
                job["stages"][stage] = stage_data
                append_log(job, stage, f"Done — {len(view_pairs)} views reviewed, {len(output)} chars{note}")
                save_job(job)
                return {"ok": True, "stage": stage, "output": output, "handled_by": handled_by}

        raw, handled_by, tok = await _call_with_fallback(stage, harness, persona, user, roles, job)
        _accum_tokens(job, stage, tok)
        output = _strip_all_fences(_strip_fences(raw))

        # Reviewer escalation: count issues, fix inline if within threshold
        if stage == "reviewer" and output.strip().upper().startswith("REJECTED"):
            from app.pipeline_rules import reviewer_threshold
            issue_count = _count_rejection_issues(output)
            threshold = reviewer_threshold()
            if issue_count <= threshold:
                review_notes = output
                append_log(job, stage, f"Review notes ({issue_count} issues): {output[:600]}")
                append_log(job, stage, f"Fixing {issue_count} issues inline")
                fix_user = (
                    f"Issues found:\n{output}\n\n"
                    f"Original YAML:\n{prev}\n\n"
                    f"Fix ALL listed issues. Output corrected YAML only. No explanations."
                )
                try:
                    raw2, handled_by, tok2 = await _call_with_fallback(stage, harness, persona, fix_user, roles, job)
                    _accum_tokens(job, stage, tok2)
                    output = _strip_all_fences(_strip_fences(raw2))
                except Exception:
                    pass  # Keep rejection if fix attempt fails
            else:
                review_notes = None
        else:
            review_notes = None

        # Reviewer: extract leading # REVIEW: comment as notes, pass clean YAML downstream
        if stage == "reviewer" and not review_notes:
            lines = output.splitlines()
            comment_lines = []
            yaml_lines = []
            for line in lines:
                if not yaml_lines and line.strip().startswith("#"):
                    comment_lines.append(line)
                else:
                    yaml_lines.append(line)
            if comment_lines:
                review_notes = "\n".join(comment_lines).replace("# REVIEW:", "").replace("#", "").strip()
                output = "\n".join(yaml_lines).strip()
            # If output is empty after stripping (model wrote verdict only), use assembler input
            if not output.strip() and prev:
                output = prev
                review_notes = (review_notes or "") + " (reviewer returned no YAML — using assembler output)"

        write_stage_output(job_id, stage, output)
        note = f" (handled by {handled_by})" if handled_by != stage else ""
        stage_data: dict = {
            "status": "done", "preview": output[:400], "handled_by": handled_by,
            "model_name": harness.get("display_name", harness_id),
            "ctx_window": harness.get("context_window"),
        }
        # carry over accumulated tokens from _accum_tokens calls above
        if job.get("stages", {}).get(stage, {}).get("tokens"):
            stage_data["tokens"] = job["stages"][stage]["tokens"]
        if review_notes:
            stage_data["review_notes"] = review_notes
            append_log(job, stage, f"Review: {review_notes[:300]}")
        job["stages"][stage] = stage_data
        append_log(job, stage, f"Done — {len(output)} chars{note}")
        save_job(job)
        return {"ok": True, "stage": stage, "output": output, "handled_by": handled_by}

    except Exception as exc:
        job["stages"][stage] = {"status": "error", "error": str(exc),
                                "model_name": harness.get("display_name", harness_id),
                                "ctx_window": harness.get("context_window")}
        append_log(job, stage, f"ERROR — all workers exhausted: {exc}")
        save_job(job)
        return {"ok": False, "error": str(exc)}


async def _call_assembler(job_id: str, fragments: list[dict[str, str]], roles: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """Assemble block-by-block: one model call per block, then merge views in Python."""
    from app.pipeline_prompts import render_prompt
    assignment = roles.get("assembler", {})
    harness_id = assignment.get("harness_id")
    harness = get_harness(harness_id) if harness_id else None

    spec = job.get("spec", "")

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
    model_name = harness.get("display_name", harness_id)

    # Group fragments by block
    blocks: dict[str, list[dict]] = {}
    for f in fragments:
        blocks.setdefault(f["block"], []).append(f)

    append_log(job, "assembler", f"Assembling {len(blocks)} blocks ({len(fragments)} fragments) with {model_name}...")
    job["stages"]["assembler"] = {"status": "running"}
    save_job(job)

    assembled_views: list[str] = []
    all_inputs: list[str] = []
    last_handled_by = harness_id

    try:
        for block_name, block_frags in blocks.items():
            if is_cancelled(job_id):
                return {"ok": False, "error": "cancelled"}

            frags_text = "\n".join(
                f"--- Card {i+1} (Task: {f['task']}) ---\n{f['yaml']}"
                for i, f in enumerate(block_frags)
            )
            block_prompt = (
                f"Block name: {block_name}\n\n"
                f"Card fragments:\n{frags_text}\n\n"
                "Combine these cards into a single Lovelace view section.\n"
                "Output ONLY a YAML list of cards (starting with '- type:'), no views wrapper, no title, no dashboard structure.\n"
                "Fix any invalid card fields. YAML only. No fences. No explanation."
            )
            all_inputs.append(f"=== Block: {block_name} ===\n{block_prompt}")
            append_log(job, "assembler", f"Block: {block_name} ({len(block_frags)} cards)")
            job["stages"]["assembler"]["progress"] = f"{len(assembled_views)+1}/{len(blocks)} blocks"
            save_job(job)

            raw, last_handled_by, tok_a = await _call_with_fallback("assembler", harness, persona, block_prompt, roles, job)
            _accum_tokens(job, "assembler", tok_a)
            cards_yaml = _strip_all_fences(_strip_fences(raw)).strip()
            assembled_views.append((block_name, cards_yaml))

        # Save combined input for inspection
        write_stage_input(job_id, "assembler", "\n\n".join(all_inputs))

        # Python-merge: build full dashboard structure
        spec_title = spec.split("\n")[0][:60].strip() or "Dashboard"
        views_yaml = "\n".join(
            f"  - title: {name}\n    cards:\n" + "\n".join(
                f"      {line}" for line in cards.splitlines()
            )
            for name, cards in assembled_views
        )
        output = f"title: {spec_title}\nviews:\n{views_yaml}"

        write_stage_output(job_id, "assembler", output)
        asm_stage: dict = {
            "status": "done", "preview": output[:400], "handled_by": last_handled_by,
            "model_name": harness.get("display_name", harness_id),
            "ctx_window": harness.get("context_window"),
        }
        if job.get("stages", {}).get("assembler", {}).get("tokens"):
            asm_stage["tokens"] = job["stages"]["assembler"]["tokens"]
        job["stages"]["assembler"] = asm_stage
        append_log(job, "assembler", f"Done — {len(assembled_views)} views, {len(output)} chars")
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
            raw, handled_by, tok_g = await _call_with_fallback("generator", harness, persona, user, roles, job)
            _accum_tokens(job, "generator", tok_g)
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
            raw, _, tok_g = await _call_with_fallback("generator", harness, persona, user, roles, job)
            _accum_tokens(job, "generator", tok_g)
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

    sem = _get_pipeline_sem()
    if sem.locked():
        append_log(job, "pipeline", "Queued — waiting for current job to finish")
        save_job(job)

    async with sem:
        await _run_pipeline_inner(job_id)


async def _run_pipeline_inner(job_id: str) -> None:
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
        elif stage == "assembler" and "generator" in pipeline and "manager" in pipeline:
            # Assembler is run inside _run_generator_loop — skip the standalone stage call.
            # Just read what the generator loop already wrote and continue.
            job = load_job(job_id)
            if job.get("stages", {}).get("assembler", {}).get("status") == "done":
                assembler_out = read_stage_output(job_id, "assembler")
                prev_output = assembler_out or prev_output
                continue
            # Assembler wasn't run (e.g., single-task fallback skipped it) — run it now
            result = await run_stage(job_id, stage)
        else:
            result = await run_stage(job_id, stage)
        if not result["ok"]:
            job = load_job(job_id)
            job["status"] = STATUS_FAILED
            save_job(job)
            return
        prev_output = result.get("output")
        job = load_job(job_id)

        # QUESTION: escalation — worker is stuck, route to advisor and retry once
        if prev_output and prev_output.strip().upper().startswith("QUESTION:") and stage != "supervisor":
            question_text = prev_output.strip()[9:].strip()
            advisor_harness_id = load_roles().get("advisor", {}).get("harness_id")
            advisor_harness = get_harness(advisor_harness_id) if advisor_harness_id else None
            if advisor_harness:
                advisor_persona = ROLE_META.get("advisor", {}).get("persona", "")
                advisor_prompt = (
                    f"A {stage} worker is stuck on this job and needs guidance.\n\n"
                    f"Job spec: {job.get('spec','')}\n\n"
                    f"Their question: {question_text}\n\n"
                    "Provide a clear, specific answer. Be concise."
                )
                try:
                    raw_answer, _, tok_adv = await _call_with_fallback("advisor", advisor_harness, advisor_persona, advisor_prompt, load_roles(), job)
                    _accum_tokens(job, "advisor", tok_adv)
                    answer = _strip_fences(raw_answer)
                    append_log(job, "advisor", f"Q: {question_text[:80]} → answered ({len(answer)} chars)")
                    job["stage_instructions"] = job.get("stage_instructions", {})
                    job["stage_instructions"][stage] = f"Advisor guidance: {answer}"
                    save_job(job)
                    # Retry the stage with the advisor's answer
                    result = await run_stage(job_id, stage)
                    if not result["ok"]:
                        job = load_job(job_id)
                        job["status"] = STATUS_FAILED
                        save_job(job)
                        return
                    prev_output = result.get("output")
                    job = load_job(job_id)
                except Exception as e:
                    append_log(job, "advisor", f"Escalation failed: {e}")
                    save_job(job)

        # Supervisor empty output — treat as failed, don't silently pass
        if stage == "supervisor" and not (prev_output or "").strip():
            job = load_job(job_id)
            append_log(job, "supervisor", "Empty output — treating as rejection, use ↺ Retry")
            job["status"] = STATUS_FAILED
            save_job(job)
            return

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
                    elif rerun_stage == "assembler" and "generator" in pipeline and "manager" in pipeline:
                        rjob = load_job(job_id)
                        if rjob.get("stages", {}).get("assembler", {}).get("status") == "done":
                            continue
                        r = await run_stage(job_id, rerun_stage)
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
                # If the rerun supervisor also rejected, fail — don't swallow it as done
                if prev_output and prev_output.strip().upper().startswith("REJECTED"):
                    job = load_job(job_id)
                    job["rejection_feedback"] = prev_output
                    append_log(job, "supervisor", "Second rejection after rerun — use ↺ Retry")
                    job["status"] = STATUS_FAILED
                    save_job(job)
                    from app.sensor_push import push_pipeline_sensors
                    await push_pipeline_sensors()
                    return
            else:
                job = load_job(job_id)
                job["rejection_feedback"] = prev_output
                append_log(job, stage, "Rejected — use ↺ Retry to rerun with adjusted settings")
                job["status"] = STATUS_FAILED
                save_job(job)
                from app.sensor_push import push_pipeline_sensors
                await push_pipeline_sensors()
                return

    # All stages passed
    if prev_output:
        write_stage_output(job_id, "final", prev_output)
        job = load_job(job_id)
        job["final_output"] = prev_output[:600]

    job["status"] = STATUS_DONE
    append_log(job, "pipeline", "All stages complete")
    save_job(job)

    from app.sensor_push import push_pipeline_sensors
    await push_pipeline_sensors()

    if job.get("type") == "ha_dashboard" and prev_output and not prev_output.strip().upper().startswith("REJECTED"):
        await _push_dashboard(job, prev_output)


async def _push_dashboard(job: dict[str, Any], yaml_content: str) -> None:
    import os
    import json
    import yaml as _yaml
    import websockets

    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        append_log(job, "ha_push", "No SUPERVISOR_TOKEN — output saved to run file only")
        save_job(job)
        return

    try:
        dashboard_id = job.get("target_dashboard", "fleet-output")
        config_dict = _yaml.safe_load(yaml_content)

        ws_url = "ws://supervisor/core/websocket"
        async with websockets.connect(ws_url) as ws:
            # Auth handshake
            msg = json.loads(await ws.recv())
            if msg.get("type") != "auth_required":
                raise RuntimeError(f"Unexpected WS msg: {msg}")
            await ws.send(json.dumps({"type": "auth", "access_token": token}))
            auth_result = json.loads(await ws.recv())
            if auth_result.get("type") != "auth_ok":
                raise RuntimeError(f"WS auth failed: {auth_result}")

            # Check if dashboard already exists
            await ws.send(json.dumps({"id": 1, "type": "lovelace/dashboards/list"}))
            list_result = json.loads(await ws.recv())
            existing = [d.get("url_path") for d in list_result.get("result", [])]
            _flog(f"  existing dashboards: {existing}")

            if dashboard_id not in existing:
                # Create it first
                await ws.send(json.dumps({
                    "id": 2, "type": "lovelace/dashboards/create",
                    "url_path": dashboard_id,
                    "title": dashboard_id.replace("-", " ").title(),
                    "icon": "mdi:robot-industrial",
                    "show_in_sidebar": True, "require_admin": False,
                    "mode": "storage",
                }))
                create_result = json.loads(await ws.recv())
                _flog(f"  dashboard create: {create_result}")
                if not create_result.get("success"):
                    raise RuntimeError(f"Dashboard create failed: {create_result.get('error', create_result)}")

            # Save config
            await ws.send(json.dumps({
                "id": 3, "type": "lovelace/config/save",
                "url_path": dashboard_id,
                "config": config_dict,
            }))
            result = json.loads(await ws.recv())
            ok = result.get("success", False)
            detail = "" if ok else f" — {result.get('error', {}).get('message', str(result))}"
            append_log(job, "ha_push", f"Dashboard push {'OK' if ok else 'FAILED'}{detail}")

    except Exception as exc:
        append_log(job, "ha_push", f"HA push error — {exc}")

    save_job(job)
