from __future__ import annotations

import os
import httpx

_HA_API = "http://supervisor/core/api"


def _token() -> str:
    return os.environ.get("SUPERVISOR_TOKEN", "")


async def push_sensor(entity_id: str, state: str | int, attributes: dict) -> None:
    token = _token()
    if not token:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{_HA_API}/states/{entity_id}",
                headers={"Authorization": f"Bearer {token}"},
                json={"state": str(state), "attributes": attributes},
            )
    except Exception:
        pass


async def push_pipeline_sensors() -> None:
    """Push Fleet Command pipeline metrics to HA following the fleet schema."""
    from app.jobs import list_jobs
    from app.harnesses import load_harnesses
    from app.roles import load_roles
    from datetime import datetime, timezone

    jobs = list_jobs(limit=200)
    today = datetime.now(timezone.utc).date().isoformat()
    now_ts = datetime.now(timezone.utc).strftime("%d/%m/%y %H:%M")

    # ── Workers (staff schema) ──────────────────────────────────────────────
    harnesses = load_harnesses()
    roles = load_roles()

    # Build reverse role map: harness_id → [roles]
    harness_roles: dict[str, list[str]] = {}
    for role_name, assignment in roles.items():
        hid = assignment.get("harness_id", "")
        if hid:
            harness_roles.setdefault(hid, []).append(role_name)

    # Accumulate token totals per harness across all jobs
    harness_tokens: dict[str, dict[str, int]] = {}
    for j in jobs:
        for stage, sdata in j.get("stages", {}).items():
            hid = sdata.get("harness_used") or sdata.get("harness", "")
            tok = sdata.get("tokens", {})
            if hid:
                if hid not in harness_tokens:
                    harness_tokens[hid] = {"input": 0, "output": 0, "jobs": 0}
                harness_tokens[hid]["input"] += tok.get("input", 0)
                harness_tokens[hid]["output"] += tok.get("output", 0)
                harness_tokens[hid]["jobs"] += 1

    workers = []
    for idx, (hid, h) in enumerate(harnesses.items(), start=1):
        tok = harness_tokens.get(hid, {"input": 0, "output": 0, "jobs": 0})
        total_tok = tok["input"] + tok["output"]
        workers.append({
            "id": idx,
            "name": h.get("display_name", hid),
            "role": ", ".join(harness_roles.get(hid, ["unassigned"])),
            "model": h.get("model", ""),
            "cost_type": h.get("cost_type", "unknown"),
            "context_window": h.get("context_window"),
            "status": "Active" if hid in harness_roles else "Standby",
            "tokens_in": tok["input"],
            "tokens_out": tok["output"],
            "tokens_total": total_tok,
            "jobs_handled": tok["jobs"],
        })

    active_workers = sum(1 for w in workers if w["status"] == "Active")

    await push_sensor("sensor.fleet_command_workers", active_workers, {
        "friendly_name": "Fleet Command Workers",
        "icon": "mdi:robot-outline",
        "total": len(workers),
        "active": active_workers,
        "unit_of_measurement": "workers",
        "workers": workers,
    })

    # ── Jobs (tasks schema) ─────────────────────────────────────────────────
    total = len(jobs)
    done = sum(1 for j in jobs if j.get("status") == "done")
    failed = sum(1 for j in jobs if j.get("status") == "failed")
    running = sum(1 for j in jobs if j.get("status") == "running")
    pending = sum(1 for j in jobs if j.get("status") == "pending")
    today_jobs = [j for j in jobs if j.get("created_at", "").startswith(today)]

    task_list = []
    for j in jobs[:50]:
        stages = j.get("stages", {})
        tok = j.get("tokens_total", {"input": 0, "output": 0})
        workers_used = list({
            sdata.get("harness_used") or sdata.get("harness", "")
            for sdata in stages.values()
            if sdata.get("harness_used") or sdata.get("harness")
        })
        task_list.append({
            "id": j["id"],
            "name": (j.get("spec", "")[:60] + "...") if len(j.get("spec", "")) > 60 else j.get("spec", ""),
            "type": j.get("type", ""),
            "status": j.get("status", ""),
            "pipeline": j.get("pipeline", []),
            "workers": workers_used,
            "tokens_in": tok.get("input", 0),
            "tokens_out": tok.get("output", 0),
            "started": j.get("created_at", ""),
            "target": j.get("target_dashboard", ""),
        })

    await push_sensor("sensor.fleet_command_jobs", running + pending, {
        "friendly_name": "Fleet Command Jobs",
        "icon": "mdi:briefcase-outline",
        "total": total,
        "done": done,
        "failed": failed,
        "running": running,
        "pending": pending,
        "today_total": len(today_jobs),
        "today_done": sum(1 for j in today_jobs if j.get("status") == "done"),
        "today_failed": sum(1 for j in today_jobs if j.get("status") == "failed"),
        "unit_of_measurement": "active",
        "tasks": task_list,
    })

    # ── Pipeline stages (blocks schema) ─────────────────────────────────────
    stage_stats: dict[str, dict] = {}
    for j in jobs:
        for stage, sdata in j.get("stages", {}).items():
            s = stage_stats.setdefault(stage, {
                "name": stage,
                "total_runs": 0,
                "done": 0,
                "failed": 0,
                "tokens_in": 0,
                "tokens_out": 0,
            })
            s["total_runs"] += 1
            if sdata.get("status") == "done":
                s["done"] += 1
            elif sdata.get("status") == "failed":
                s["failed"] += 1
            tok = sdata.get("tokens", {})
            s["tokens_in"] += tok.get("input", 0)
            s["tokens_out"] += tok.get("output", 0)

    blocks = list(stage_stats.values())

    await push_sensor("sensor.fleet_command_stages", len(blocks), {
        "friendly_name": "Fleet Command Pipeline Stages",
        "icon": "mdi:pipe",
        "unit_of_measurement": "stages",
        "blocks": blocks,
    })
