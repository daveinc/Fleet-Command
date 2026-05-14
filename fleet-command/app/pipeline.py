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


# Known aliases for invalid REJECTED_AT stage names the supervisor sometimes invents
_REJECTION_ALIASES: dict[str, str] = {
    "final_output":    "reviewer",
    "final":           "reviewer",
    "output":          "reviewer",
    "assembly":        "assembler",
    "assembling":      "assembler",
    "generation":      "generator",
    "generating":      "generator",
    "dev":             "generator",
    "developer":       "generator",
    "planning":        "manager",
    "plan":            "manager",
    "breakdown":       "manager",
    "pm":              "project_manager",
    "project":         "project_manager",
    "scoping":         "project_manager",
    "review":          "reviewer",
    "reviewing":       "reviewer",
    "qa":              "reviewer",
}

_PIPELINE_VALID_STAGES = {"project_manager", "manager", "generator", "assembler", "reviewer"}


def _remap_rejection_target(raw: str, pipeline: list[str]) -> str | None:
    """Map an invalid REJECTED_AT value to the nearest valid pipeline stage."""
    raw = raw.lower().strip()
    # Direct alias lookup
    if raw in _REJECTION_ALIASES:
        candidate = _REJECTION_ALIASES[raw]
        if candidate in pipeline:
            return candidate
    # Substring match — e.g. "generator_stage" → "generator"
    for stage in pipeline:
        if stage in raw or raw in stage:
            return stage
    # Default: generator is the most common root cause
    return "generator" if "generator" in pipeline else pipeline[0] if pipeline else None


def _estimate_input_tokens(system: str, user: str) -> int:
    """Rough estimate: 1 token ≈ 4 chars."""
    return (len(system) + len(user)) // 4


def _safe_output_budget(harness: dict[str, Any], system: str, user: str, default: int = 1024) -> int:
    """Return max output tokens that fits within the harness context window."""
    explicit = harness.get("token_allowance")
    try:
        explicit_allowance = int(explicit) if explicit else None
    except (TypeError, ValueError):
        explicit_allowance = None

    ctx = harness.get("context_window")
    if not ctx:
        return explicit_allowance or default
    estimated_input = _estimate_input_tokens(system, user)
    # Leave 10% headroom for safety
    remaining = int(ctx * 0.9) - estimated_input
    limit = explicit_allowance or default
    return max(256, min(remaining, limit))


def _overflow_message(info: dict[str, Any], harness: dict[str, Any]) -> str:
    model_name = harness.get("display_name", harness.get("model", "?"))
    if info.get("reason") == "unknown_context_window":
        return (
            f"Input overflow guard cannot run because context window is unknown "
            f"for {model_name}; estimated input is ~{info['estimated']} tokens"
        )
    return (
        f"Input overflow: ~{info['estimated']} tokens estimated "
        f"vs {info['ctx_window']} ctx window ({info['pct']}% used) "
        f"on {model_name}"
    )


def _build_payload(harness: dict[str, Any], system: str, user: str, model_override: str = "") -> dict[str, Any]:
    model = model_override or harness.get("model", "")
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
        msg = _overflow_message(info, harness)
        _flog(f"  OVERFLOW: {msg}")
        if job and stage:
            append_log(job, stage, f"⚠ {msg}")
        raise RuntimeError(msg)

    if ctx and info["pct"] >= 70:
        _flog(f"  ctx usage ~{info['pct']}% before call — approaching limit")

    from app.pipeline_prompts import is_modelfile_pushed, get_pushed_model, reset_modelfile_pushed
    harness_id = harness.get("_id", "")
    pushed_model = get_pushed_model(harness_id) if harness_id else None
    effective_system = "" if pushed_model else system
    model_override = pushed_model or ""

    endpoint = _resolve_endpoint(harness)
    payload = _build_payload(harness, effective_system, user, model_override)
    auth_headers = _auth_headers(harness)
    headers = {"Content-Type": "application/json", **auth_headers}
    fmt = harness.get("request_format", "ollama_chat")
    effective_model = model_override or harness.get("model", "")

    _flog(f"CALL model={effective_model}{' (pushed)' if pushed_model else ''} fmt={fmt}")
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
            # If the pushed model no longer exists in Ollama, reset and retry with base model
            if resp.status_code in (404, 500) and pushed_model and harness_id:
                try:
                    err_text = resp.json().get("error", "")
                except Exception:
                    err_text = resp.text
                if "not found" in err_text.lower() or resp.status_code == 404:
                    _flog(f"  pushed model {pushed_model!r} not found — resetting pushed flag, retrying with base model")
                    if job and stage:
                        append_log(job, stage, f"⚠ Pushed model {pushed_model!r} missing from Ollama — resetting to base model {harness.get('model')!r}")
                    reset_modelfile_pushed(harness_id)
                    base_payload = _build_payload(harness, system, user)
                    resp2 = await client.post(endpoint, headers=headers, json=base_payload)
                    elapsed = time.monotonic() - t0
                    _flog(f"  retry status={resp2.status_code} elapsed={elapsed:.1f}s size={len(resp2.content)}b")
                    resp2.raise_for_status()
                    result, tokens = _extract(resp2, fmt)
                    _flog(f"  extracted={len(result)} chars (base fallback) preview={result[:120].strip()!r}")
                    return result, tokens
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
    from app.message_builder import overflow_info, harness_can_respond

    # Use harness_id strings as identity — harness dicts have no "id" field
    primary_harness_id = roles.get(stage, {}).get("harness_id", "") or ""
    tried_harness_ids: set[str] = set()

    if not harness_can_respond(primary_harness):
        _flog(f"  ESCALATE: primary harness token_allowance too small for useful response")
        append_log(job, stage, "⚠ Primary worker token allowance insufficient — escalating")
        primary_info = overflow_info(system, user, primary_harness)
        primary_overflows = True
    else:
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
        if not harness_can_respond(fb_harness):
            _flog(f"  SKIP {fallback_role}/{fb_harness_id} — token_allowance too small")
            continue
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


async def _call_stage_chunked(
    stage: str,
    harness: dict[str, Any],
    persona: str,
    items: list[str],
    fixed_prefix: str,
    prompt_fn: Any,
    roles: dict[str, Any],
    job: dict[str, Any],
    chunk_label: str = "chunk",
) -> list[tuple[str, str, dict[str, int]]]:
    """Split items into context-fitting batches and call _call_with_fallback per batch.

    prompt_fn(batch_text: str) -> str builds the user prompt for each batch.
    Returns list of (raw_output, handled_by, token_counts) per batch.
    Single-item batches that are still too large escalate inside _call_with_fallback.
    """
    from app.message_builder import chunk_to_fit

    batches = chunk_to_fit(items, harness, fixed_prefix=fixed_prefix)
    results = []
    for i, batch in enumerate(batches):
        if len(batches) > 1:
            append_log(job, stage, f"{chunk_label} {i + 1}/{len(batches)} ({len(batch)} items)")
        user_prompt = prompt_fn("\n".join(batch))
        raw, handled_by, tok = await _call_with_fallback(stage, harness, persona, user_prompt, roles, job)
        results.append((raw, handled_by, tok))
    return results


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
        # Detect new view entry — handle 0 or 2 space indent (assembler uses 0)
        m = re.match(r'^\s{0,2}-\s+title:\s*(.+)', line)
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


def _domains_in_text(text: str) -> set[str]:
    return {
        match.group(1)
        for match in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.[a-zA-Z0-9_]+", text)
    }


async def _fetch_ha_entities(spec: str, yaml_input: str = "", token_budget: int | None = None) -> str:
    """Fetch dashboard-relevant entities from HA Supervisor API."""
    import os
    DASHBOARD_DOMAINS = {
        "sensor", "binary_sensor", "switch", "light", "media_player",
        "weather", "climate", "camera", "automation", "script",
        "input_boolean", "input_number", "input_select", "input_text",
        "input_datetime", "person", "device_tracker", "cover",
        "fan", "vacuum", "water_heater", "alarm_control_panel",
    }
    requested_domains = (_domains_in_text(yaml_input) | _domains_in_text(spec)) & DASHBOARD_DOMAINS
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        return "(HA entity list unavailable — no SUPERVISOR_TOKEN)"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "http://supervisor/core/api/states",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            states = resp.json()
        prioritized: list[str] = []
        fallback: list[str] = []
        for s in states:
            eid = s.get("entity_id", "")
            domain = eid.split(".")[0] if "." in eid else ""
            if domain not in DASHBOARD_DOMAINS:
                continue
            state = s.get("state", "")
            attrs = s.get("attributes", {})
            friendly = attrs.get("friendly_name", "")
            label = eid
            if friendly and friendly.lower() != eid.replace("_", " ").lower():
                label += f" ({friendly})"
            label += f" — {state}"
            if not requested_domains or domain in requested_domains:
                prioritized.append(label)
            else:
                fallback.append(label)
        lines = prioritized or fallback
        if token_budget is not None:
            token_budget = max(0, token_budget)
            used = 0
            trimmed = []
            for line in lines:
                line_tokens = _estimate_input_tokens("", line + "\n")
                if used + line_tokens > token_budget:
                    break
                trimmed.append(line)
                used += line_tokens
            if len(trimmed) < len(lines):
                trimmed.append(f"... ({len(trimmed)} shown, trimmed to fit reviewer context budget)")
            lines = trimmed
        return "\n".join(lines) if lines else "(no relevant entities found)"
    except Exception as exc:
        return f"(entity fetch failed: {exc})"


async def _run_reviewer_3pass(
    job_id: str,
    harness: dict[str, Any],
    persona: str,
    spec: str,
    yaml_input: str,
    roles: dict[str, Any],
    job: dict[str, Any],
) -> dict[str, Any]:
    """3-pass reviewer with view-level chunking: entity IDs → structure → card_mod."""
    import yaml as _yaml
    from app.message_builder import chunk_to_fit

    harness_id = roles.get("reviewer", {}).get("harness_id", "reviewer")
    append_log(job, "reviewer", "Starting 3-pass review...")
    job["stages"]["reviewer"] = {"status": "running"}
    save_job(job)

    _STRUCTURE_RULE = (
        "CRITICAL STRUCTURE RULE: A Lovelace dashboard root MUST have 'title:' and 'views:' keys. "
        "NEVER output 'type:', 'cards:', or 'entities:' at root level. "
        "If the input has correct title+views structure, preserve it exactly."
    )

    def _safe(candidate: str, fallback: str) -> str:
        if "views:" not in candidate:
            return fallback
        # Reject truncated output — if candidate is less than 40% of fallback, treat as bad
        if fallback and len(candidate) < len(fallback) * 0.4:
            return fallback
        return candidate

    def _reassemble(chunks: list[str], title: str) -> str:
        all_views: list = []
        for chunk in chunks:
            try:
                parsed = _yaml.safe_load(chunk)
                if isinstance(parsed, dict):
                    all_views.extend(parsed.get("views", []))
            except Exception:
                pass
        if not all_views:
            return chunks[0] if chunks else ""
        return _yaml.dump({"title": title, "views": all_views},
                          default_flow_style=False, allow_unicode=True, sort_keys=False)

    def _view_items(yaml_text: str) -> list[str]:
        views = _split_yaml_views(yaml_text)
        return [f"  - title: {t}\n{c}" for t, c in views] if views else [yaml_text]

    def _chunk_yaml(batch: list[str], title: str) -> str:
        return f"title: {title}\nviews:\n" + "\n".join(batch)

    try:
        _p = _yaml.safe_load(yaml_input)
        dash_title = _p.get("title", "Dashboard") if isinstance(_p, dict) else "Dashboard"
    except Exception:
        dash_title = "Dashboard"

    last_handled = "reviewer"

    # ── Pass 1 — Entity ID resolution ──────────────────────────────────────
    entity_list = await _fetch_ha_entities(spec, yaml_input=yaml_input, token_budget=None)
    p1_fixed = (
        f"Spec: {spec}\n\nAvailable Home Assistant entities:\n{entity_list}\n\n"
        "Replace ALL placeholder or incorrect entity IDs with real entity IDs from the list above. "
        "Match by domain and purpose. If unsure, pick the closest available entity. "
        "Do NOT change card structure, layout, or root dashboard structure. "
        f"{_STRUCTURE_RULE} "
        "Output corrected YAML only. No fences. No explanation.\n\nDashboard YAML:\n"
    )
    items1 = _view_items(yaml_input)
    batches1 = chunk_to_fit(items1, harness, fixed_prefix=p1_fixed)
    append_log(job, "reviewer", f"Pass 1: entity resolution — {len(items1)} views, {len(batches1)} chunk(s)")
    yaml1 = yaml_input
    try:
        results1: list[str] = []
        for i, batch in enumerate(batches1):
            if len(batches1) > 1:
                append_log(job, "reviewer", f"Pass 1 chunk {i+1}/{len(batches1)}")
            prompt = p1_fixed + _chunk_yaml(batch, dash_title)
            write_stage_input(job_id, "reviewer", f"[Pass 1 chunk {i+1}/{len(batches1)}]\n{prompt}")
            raw, last_handled, tok = await _call_with_fallback("reviewer", harness, persona, prompt, roles, job)
            _accum_tokens(job, "reviewer", tok)
            results1.append(_strip_all_fences(_strip_fences(raw)))
        c1 = _reassemble(results1, dash_title) if len(results1) > 1 else (results1[0] if results1 else yaml_input)
        yaml1 = _safe(c1, yaml_input)
        if "views:" not in c1:
            append_log(job, "reviewer", "Pass 1 dropped views: — keeping input")
        append_log(job, "reviewer", f"Pass 1 done — {len(yaml1)} chars, model={harness.get('display_name', harness_id)}")
    except Exception as exc:
        append_log(job, "reviewer", f"Pass 1 failed: {exc} — using input")

    # ── Pass 2 — Container / structure ─────────────────────────────────────
    p2_fixed = (
        "Review this Home Assistant Lovelace dashboard YAML for container and assembly issues.\n"
        "Check: valid card types (sensor/entities/gauge/weather-forecast/history-graph/button/markdown/grid/vertical-stack), "
        "correct nesting (entities card uses list under 'entities:' key, not 'entity:'), "
        "no extra/invalid fields, proper view → cards hierarchy. "
        f"{_STRUCTURE_RULE} "
        "Fix all issues. Output corrected YAML only. No fences. No explanation.\n\nDashboard YAML:\n"
    )
    items2 = _view_items(yaml1)
    batches2 = chunk_to_fit(items2, harness, fixed_prefix=p2_fixed)
    append_log(job, "reviewer", f"Pass 2: structure — {len(items2)} views, {len(batches2)} chunk(s)")
    yaml2 = yaml1
    try:
        results2: list[str] = []
        for i, batch in enumerate(batches2):
            if len(batches2) > 1:
                append_log(job, "reviewer", f"Pass 2 chunk {i+1}/{len(batches2)}")
            prompt = p2_fixed + _chunk_yaml(batch, dash_title)
            raw, last_handled, tok = await _call_with_fallback("reviewer", harness, persona, prompt, roles, job)
            _accum_tokens(job, "reviewer", tok)
            results2.append(_strip_all_fences(_strip_fences(raw)))
        c2 = _reassemble(results2, dash_title) if len(results2) > 1 else (results2[0] if results2 else yaml1)
        yaml2 = _safe(c2, yaml1)
        if "views:" not in c2:
            append_log(job, "reviewer", "Pass 2 dropped views: — keeping pass 1")
        append_log(job, "reviewer", f"Pass 2 done — {len(yaml2)} chars, model={harness.get('display_name', harness_id)}")
    except Exception as exc:
        append_log(job, "reviewer", f"Pass 2 failed: {exc} — using pass 1")

    # ── Pass 3 — card_mod / styles (conditional) ───────────────────────────
    yaml3 = yaml2
    if "card_mod" not in yaml2 and "card_mod" not in spec.lower():
        append_log(job, "reviewer", "Pass 3: no card_mod — skipping")
    else:
        p3_fixed = (
            "Review this Home Assistant Lovelace dashboard YAML for card_mod and style issues.\n"
            "Check: card_mod sections have valid CSS syntax, style targets correct elements (card, :host, ha-card), "
            "no invalid card_mod fields, all style blocks properly indented under card_mod. "
            "If no card_mod sections present, output YAML unchanged. "
            f"{_STRUCTURE_RULE} "
            "Fix all issues. Output corrected YAML only. No fences. No explanation.\n\nDashboard YAML:\n"
        )
        items3 = _view_items(yaml2)
        batches3 = chunk_to_fit(items3, harness, fixed_prefix=p3_fixed)
        append_log(job, "reviewer", f"Pass 3: card_mod — {len(items3)} views, {len(batches3)} chunk(s)")
        try:
            results3: list[str] = []
            for i, batch in enumerate(batches3):
                if len(batches3) > 1:
                    append_log(job, "reviewer", f"Pass 3 chunk {i+1}/{len(batches3)}")
                prompt = p3_fixed + _chunk_yaml(batch, dash_title)
                raw, last_handled, tok = await _call_with_fallback("reviewer", harness, persona, prompt, roles, job)
                _accum_tokens(job, "reviewer", tok)
                results3.append(_strip_all_fences(_strip_fences(raw)))
            c3 = _reassemble(results3, dash_title) if len(results3) > 1 else (results3[0] if results3 else yaml2)
            yaml3 = _safe(c3, yaml2)
            if "views:" not in c3:
                append_log(job, "reviewer", "Pass 3 dropped views: — keeping pass 2")
            append_log(job, "reviewer", f"Pass 3 done — {len(yaml3)} chars, model={harness.get('display_name', harness_id)}")
        except Exception as exc:
            append_log(job, "reviewer", f"Pass 3 failed: {exc} — using pass 2")

    if not yaml3.strip():
        yaml3 = yaml_input

    write_stage_output(job_id, "reviewer", yaml3)
    note = f" (handled by {last_handled})" if last_handled != "reviewer" else ""
    stage_data: dict = {
        "status": "done",
        "preview": yaml3[:400],
        "handled_by": last_handled,
        "model_name": harness.get("display_name", harness_id),
        "ctx_window": harness.get("context_window"),
    }
    if job.get("stages", {}).get("reviewer", {}).get("tokens"):
        stage_data["tokens"] = job["stages"]["reviewer"]["tokens"]
    job["stages"]["reviewer"] = stage_data
    append_log(job, "reviewer", f"3-pass review complete — {len(yaml3)} chars{note}")
    save_job(job)
    return {"ok": True, "stage": "reviewer", "output": yaml3, "handled_by": last_handled}


async def _handle_escalation(
    stage: str,
    reason: str,
    context: str,
    roles: dict[str, Any],
    job: dict[str, Any],
) -> str | None:
    """Escalate one level up the chain. Returns guidance text or None if escalation failed."""
    from app.pipeline_rules import get_escalation_target

    target_stage = get_escalation_target(stage)
    if not target_stage:
        _flog(f"  ESCALATE: no target defined for {stage}")
        return None

    assignment = roles.get(target_stage, {})
    target_harness = get_harness(assignment.get("harness_id", "")) if assignment.get("harness_id") else None
    if not target_harness:
        _flog(f"  ESCALATE: no harness for {target_stage}")
        return None

    target_persona = ROLE_META.get(target_stage, {}).get("persona", "You are a helpful AI assistant.")
    target_persona = target_persona.split(".")[0] + "."

    escalation_prompt = (
        f"A {stage} worker is escalating to you — it cannot complete its task.\n\n"
        f"Reason: {reason}\n\n"
        f"Task context:\n{context[:1200]}\n\n"
        "Provide clear, specific guidance on how to proceed. "
        "You may: clarify the task, provide missing information, split it differently, or specify a different approach. "
        "Be concise and actionable. Your response will be passed directly back to the worker."
    )

    append_log(job, stage, f"Escalating to {target_stage}: {reason[:100]}")
    _flog(f"  ESCALATE {stage} → {target_stage}: {reason[:80]}")

    try:
        raw, _, tok = await _call_with_fallback(target_stage, target_harness, target_persona, escalation_prompt, roles, job)
        _accum_tokens(job, target_stage, tok)
        guidance = _strip_fences(raw).strip()
        append_log(job, stage, f"Guidance from {target_stage} ({len(guidance)} chars)")
        save_job(job)
        return guidance
    except Exception as exc:
        _flog(f"  ESCALATE to {target_stage} failed: {exc}")
        append_log(job, stage, f"Escalation to {target_stage} failed: {exc}")
        return None


def _parse_fragments_text(text: str) -> list[dict[str, str]]:
    """Parse generator fragment text (# Block: name\\nyaml) back into fragment dicts."""
    fragments: list[dict[str, str]] = []
    current_block = "Main"
    current_lines: list[str] = []
    for line in text.splitlines():
        m = re.match(r'^# Block:\s*(.+)', line)
        if m:
            if current_lines:
                fragments.append({"block": current_block, "task": current_block,
                                   "yaml": "\n".join(current_lines).strip()})
                current_lines = []
            current_block = m.group(1).strip()
        else:
            current_lines.append(line)
    if current_lines:
        fragments.append({"block": current_block, "task": current_block,
                           "yaml": "\n".join(current_lines).strip()})
    return fragments


async def _run_python_assembler(job_id: str, job: dict[str, Any]) -> dict[str, Any]:
    """Python-only assembly — parse generator fragments and combine into dashboard YAML."""
    append_log(job, "assembler", "Python assembly...")
    job["stages"]["assembler"] = {"status": "running"}
    save_job(job)
    try:
        prev = read_stage_output(job_id, "generator") or ""
        if "views:" in prev:
            result = prev
        else:
            fragments = _parse_fragments_text(prev)
            result = _assemble_yaml_fragments(fragments)
        write_stage_output(job_id, "assembler", result)
        job["stages"]["assembler"] = {"status": "done", "preview": result[:400], "handled_by": "python"}
        append_log(job, "assembler", f"Done — {len(result)} chars")
        save_job(job)
        return {"ok": True, "stage": "assembler", "output": result, "handled_by": "python"}
    except Exception as exc:
        job["stages"]["assembler"] = {"status": "error", "error": str(exc)}
        append_log(job, "assembler", f"ERROR: {exc}")
        save_job(job)
        return {"ok": False, "error": str(exc)}


async def run_stage(job_id: str, stage: str) -> dict[str, Any]:
    job = load_job(job_id)
    if not job:
        return {"ok": False, "error": "job not found"}

    if stage == "assembler":
        return await _run_python_assembler(job_id, job)

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
        if stage == "reviewer":
            return await _run_reviewer_3pass(job_id, harness, persona, spec, prev or "", roles, job)

        raw, handled_by, tok = await _call_with_fallback(stage, harness, persona, user, roles, job)
        _accum_tokens(job, stage, tok)
        output = _strip_all_fences(_strip_fences(raw))

        # Escalation: worker signals it cannot complete, or returned empty output
        from app.pipeline_rules import is_worker_signal, is_trigger_enabled
        is_signal, signal_reason = is_worker_signal(output)
        is_empty = not output.strip()

        if (is_signal and is_trigger_enabled("worker_signal")) or (is_empty and is_trigger_enabled("empty_output")):
            esc_reason = signal_reason if is_signal else "empty output"
            append_log(job, stage, f"Worker escalating: {esc_reason[:120]}")
            guidance = await _handle_escalation(stage, esc_reason, user, roles, job)
            if guidance:
                job = load_job(job_id)
                instr = job.get("stage_instructions", {})
                instr[stage] = guidance
                job["stage_instructions"] = instr
                save_job(job)
                # Rebuild user prompt with guidance and retry once
                # Supervisor must never rewrite — guidance is decision context only
                if stage == "supervisor":
                    retry_extra = (
                        "Decision context from advisor — use this only to decide PASS or REJECT. "
                        "Do NOT modify or rewrite the YAML. Either return it unchanged or write REJECTED_AT:\n\n"
                        + guidance
                    )
                else:
                    retry_extra = guidance
                retry_user = _user_prompt(stage, spec, prev, extra=retry_extra)
                write_stage_input(job_id, stage, retry_user)
                append_log(job, stage, "Retrying with escalation guidance...")
                raw2, handled_by, tok2 = await _call_with_fallback(stage, harness, persona, retry_user, roles, job)
                _accum_tokens(job, stage, tok2)
                output = _strip_all_fences(_strip_fences(raw2))

        write_stage_output(job_id, stage, output)
        note = f" (handled by {handled_by})" if handled_by != stage else ""
        stage_data: dict = {
            "status": "done", "preview": output[:400], "handled_by": handled_by,
            "model_name": harness.get("display_name", harness_id),
            "ctx_window": harness.get("context_window"),
        }
        if job.get("stages", {}).get(stage, {}).get("tokens"):
            stage_data["tokens"] = job["stages"][stage]["tokens"]
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

    # Python assembly — combine fragments into complete dashboard YAML
    append_log(job, "assembler", f"Python assembly: {len(fragments)} fragments")
    job["stages"]["assembler"] = {"status": "running"}
    save_job(job)
    assembled = _assemble_yaml_fragments(fragments)
    write_stage_output(job_id, "assembler", assembled)
    job["stages"]["assembler"] = {"status": "done", "preview": assembled[:400], "handled_by": "python"}
    append_log(job, "assembler", f"Done — {len(assembled)} chars")
    save_job(job)
    return {"ok": True, "stage": "generator", "output": assembled, "handled_by": "generator"}


def _is_subjob_split(output: str) -> bool:
    return bool(re.search(r'^SUB-JOB\s+\d+:', output.strip(), re.IGNORECASE | re.MULTILINE))


def _parse_subjobs(output: str) -> list[str]:
    specs = []
    for m in re.finditer(r'SUB-JOB\s+\d+:\s*(.+?)(?=\nSUB-JOB\s+\d+:|\Z)', output.strip(), re.IGNORECASE | re.DOTALL):
        spec = m.group(1).strip()
        if spec:
            specs.append(spec)
    return specs


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
    if is_cancelled(job_id):
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

        # PM sub-job split — spawn child jobs and mark parent done
        if stage == "project_manager" and prev_output and _is_subjob_split(prev_output):
            from app.jobs import STATUS_SPLIT, create_job as _create_job
            subjob_specs = _parse_subjobs(prev_output)
            if subjob_specs:
                child_ids = []
                child_pipeline = [s for s in job.get("pipeline", []) if s != "project_manager"]
                for spec_text in subjob_specs:
                    child = _create_job({
                        "spec": spec_text,
                        "type": job.get("type", "ha_dashboard"),
                        "target_dashboard": job.get("target_dashboard", "fleet-output"),
                        "pipeline": child_pipeline,
                        "parent_job_id": job_id,
                    })
                    child_ids.append(child["id"])
                    asyncio.create_task(run_pipeline(child["id"]))
                job = load_job(job_id)
                job["status"] = STATUS_SPLIT
                job["child_job_ids"] = child_ids
                append_log(job, "project_manager", f"Split into {len(child_ids)} sub-jobs: {', '.join(child_ids)}")
                save_job(job)
                _flog(f"  JOB {job_id} split → {child_ids}")
                return

        # Supervisor empty output — treat as failed, don't silently pass
        if stage == "supervisor" and not (prev_output or "").strip():
            job = load_job(job_id)
            append_log(job, "supervisor", "Empty output — treating as rejection, use ↺ Retry")
            job["status"] = STATUS_FAILED
            save_job(job)
            return

        # Supervisor rejection — check for REJECTED_AT routing
        if stage == "supervisor" and prev_output and re.search(r'REJECTED_AT:', prev_output, re.IGNORECASE):
            match = re.search(r'REJECTED_AT:\s*(\w+)', prev_output, re.IGNORECASE)
            target = match.group(1).strip().lower() if match else None
            if target and target not in pipeline:
                target = _remap_rejection_target(target, pipeline)
                if target:
                    append_log(job, "supervisor", f"Remapped invalid REJECTED_AT to: {target}")
            if target and target in pipeline:
                job = load_job(job_id)
                job["rejection_feedback"] = prev_output
                # Extract CORRECTIVE_BRIEF and inject as stage_instructions for the target
                brief_match = re.search(r'CORRECTIVE_BRIEF:\s*(.+)', prev_output, re.IGNORECASE | re.DOTALL)
                if brief_match:
                    corrective_brief = brief_match.group(1).strip()
                    instr = job.get("stage_instructions", {})
                    instr[target] = corrective_brief
                    job["stage_instructions"] = instr
                    append_log(job, "supervisor", f"Corrective brief → {target} ({len(corrective_brief)} chars)")
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
                if prev_output and re.search(r'REJECTED_AT:', prev_output, re.IGNORECASE):
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

            msg_id = 2
            if dashboard_id not in existing:
                # Create it first
                await ws.send(json.dumps({
                    "id": msg_id, "type": "lovelace/dashboards/create",
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
                current_views: list = []
                msg_id += 1
            else:
                # Fetch existing config to merge views
                await ws.send(json.dumps({"id": msg_id, "type": "lovelace/config/get", "url_path": dashboard_id}))
                get_result = json.loads(await ws.recv())
                msg_id += 1
                existing_config = get_result.get("result", {}) or {}
                current_views = existing_config.get("views", [])
                _flog(f"  existing views: {[v.get('title') for v in current_views]}")

            # Merge job's views into existing views — update by title, append if new
            new_views = config_dict.get("views", [])
            existing_titles = {v.get("title"): i for i, v in enumerate(current_views)}
            for new_view in new_views:
                title = new_view.get("title")
                if title in existing_titles:
                    current_views[existing_titles[title]] = new_view
                    _flog(f"  updated view: {title!r}")
                else:
                    current_views.append(new_view)
                    existing_titles[title] = len(current_views) - 1
                    _flog(f"  appended view: {title!r}")

            merged_config = {**config_dict, "views": current_views}

            # Save merged config
            await ws.send(json.dumps({
                "id": msg_id, "type": "lovelace/config/save",
                "url_path": dashboard_id,
                "config": merged_config,
            }))
            result = json.loads(await ws.recv())
            ok = result.get("success", False)
            detail = "" if ok else f" — {result.get('error', {}).get('message', str(result))}"
            view_titles = [v.get("title") for v in new_views]
            append_log(job, "ha_push", f"Dashboard push {'OK' if ok else 'FAILED'} — views: {view_titles}{detail}")

    except Exception as exc:
        append_log(job, "ha_push", f"HA push error — {exc}")

    save_job(job)
