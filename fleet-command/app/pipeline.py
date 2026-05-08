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
    is_cancelled,
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


def _user_prompt(stage: str, spec: str, prev: str | None) -> str:
    if stage == "generator":
        return (
            f"{HA_REFERENCE}\n"
            f"Build this: {spec}\n"
            f"Output YAML only."
        )
    if stage == "reviewer":
        return (
            f"Job specification:\n{spec}\n\n"
            f"Output to review:\n{prev}\n\n"
            f"Return corrected YAML only. "
            f"If it cannot be fixed, write REJECTED: followed by a list of issues."
        )
    if stage == "supervisor":
        return (
            f"Job specification:\n{spec}\n\n"
            f"Final output for sign-off:\n{prev}\n\n"
            f"If acceptable, return the YAML unchanged. "
            f"If not, write REJECTED: with reasons."
        )
    return spec


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

    # Previous stage output (reviewer/supervisor need generator output)
    prev_stages = {"reviewer": "generator", "supervisor": "reviewer"}
    prev = read_stage_output(job_id, prev_stages.get(stage, "")) if stage in prev_stages else None

    user = _user_prompt(stage, spec, prev)

    append_log(job, stage, f"Calling {harness.get('display_name', harness_id)}...")
    job["stages"][stage] = {"status": "running"}
    save_job(job)

    try:
        raw = await _call_harness(harness, persona, user)
        output = _strip_fences(raw)
        write_stage_output(job_id, stage, output)
        job["stages"][stage] = {"status": "done", "preview": output[:400]}
        append_log(job, stage, f"Done — {len(output)} chars")
        save_job(job)
        return {"ok": True, "stage": stage, "output": output}

    except Exception as exc:
        job["stages"][stage] = {"status": "error", "error": str(exc)}
        append_log(job, stage, f"ERROR — {exc}")
        save_job(job)
        return {"ok": False, "error": str(exc)}


async def run_pipeline(job_id: str) -> None:
    job = load_job(job_id)
    if not job:
        return

    job["status"] = STATUS_RUNNING
    save_job(job)
    _flog(f"=== JOB {job_id} START pipeline={job.get('pipeline')} ===")

    pipeline = job.get("pipeline", ["generator"])
    prev_output: str | None = None

    for stage in pipeline:
        if is_cancelled(job_id):
            job = load_job(job_id)
            job["status"] = "cancelled"
            save_job(job)
            return

        result = await run_stage(job_id, stage)
        if not result["ok"]:
            job = load_job(job_id)  # reload to get log
            job["status"] = STATUS_FAILED
            save_job(job)
            return
        prev_output = result.get("output")

        # Reload so log is current
        job = load_job(job_id)

        # Reviewer rejection check
        if stage == "reviewer" and prev_output and prev_output.strip().upper().startswith("REJECTED"):
            job["status"] = STATUS_FAILED
            append_log(job, stage, "Pipeline halted — reviewer rejected output")
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

    if job.get("type") == "ha_dashboard" and prev_output:
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
