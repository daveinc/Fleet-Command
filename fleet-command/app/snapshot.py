# TODO: HA sensor feed — rebuild before UI overhaul. Wire to real job data (/data/jobs/).
# Current data is hardcoded. SENSOR_KEYS are the HA entity IDs. Concept is valid, impl is stale.
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os

from app.workers import configured_workers, enabled_workers


SENSOR_KEYS = ("fleet_staff", "fleet_workers", "fleet_projects", "fleet_blocks", "fleet_tasks")


def runs_dir() -> Path:
    return Path(os.getenv("RUNS_DIR", "/data/runs"))


def load_latest_run() -> dict[str, Any] | None:
    root = runs_dir()
    if not root.exists():
        return None
    latest_task: Path | None = None
    latest_mtime = -1.0
    for path in root.glob("*/task.latest.json"):
        mtime = path.stat().st_mtime
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_task = path
    if latest_task is None:
        return None
    return {
        "run_id": latest_task.parent.name,
        "task": json.loads(latest_task.read_text(encoding="utf-8")),
    }


def capabilities() -> dict[str, Any]:
    return {
        "model": "Fleet Command Add-on",
        "sensors": list(SENSOR_KEYS),
        "providers": ["ollama", "openai_compatible", "custom_http"],
        "workers": configured_workers(),
    }


def status() -> dict[str, Any]:
    latest = load_latest_run()
    task = latest["task"] if latest else {}
    run_status = task.get("status", "resting")
    phase = task.get("phase", "resting")
    active = 1 if run_status not in {"done", "failed", "resting"} else 0
    workers = configured_workers()
    live_workers = enabled_workers()

    return {
        "fleet_staff": {
            "state": staff_state(phase, run_status),
            "active": len(live_workers),
            "total": len(workers),
            "workers": workers,
            "HR": [
                {
                    "id": 1,
                    "name": "david",
                    "role": "BOSS",
                    "budget": "Infinite",
                    "status": "Available",
                    "score": "10/10",
                    "hired": "09/11/1988",
                    "reviewed": "1/1/26 - claude: Best Boss Ever!",
                    "profile": "Ingenious designer, a bit easy to lose track of, lacks some order but otherwise a relentless worker.",
                }
            ],
            "AR": [
                {
                    "id": 1,
                    "name": "claude",
                    "role": "Vice CEO",
                    "budget": "EXPENSIVE AF",
                    "status": "ON VACATION UNTIL 6/4/26",
                    "score": "10/10",
                    "hired": "",
                    "reviewed": "",
                    "profile": "",
                }
            ],
        },
        "fleet_workers": {
            "state": f"{len(live_workers)}/{len(workers)} configured",
            "active": len(live_workers),
            "total": len(workers),
            "workers": workers,
        },
        "fleet_projects": {
            "state": "Ongoing" if active else "Halted",
            "active": active,
            "total": 1,
            "projects": [
                {
                    "id": 1,
                    "name": "Coach",
                    "crew": "CEO: David, Vice CEO: Claude, Lead Reviewer: Ollama Cloud GPT5.5; Coders: Minimax M2.5 free, Cerebras AI",
                    "budget": "Full time until full release to production, planned slowdown to 1 hour a day afterwards.",
                    "status": project_state(phase, run_status),
                    "progress": progress(phase, run_status),
                    "started": "09/11/1988",
                    "reviewed": "1/1/26 - claude: Best Boss Ever!",
                    "blocks": "22 comprising of 432 tasks",
                    "estimated_time_to_completion": "6 days",
                    "profile_and_notes": task.get("summary", ""),
                    "latest_run_id": latest["run_id"] if latest else None,
                }
            ],
        },
        "fleet_blocks": {
            "state": "Ongoing" if active else "Halted",
            "active": active,
            "total": 1,
            "blocks": [
                {
                    "id": 1,
                    "project": "Coach",
                    "name": "Testing round #1",
                    "budget": "1M Tokens total between breaks of 24 hours.",
                    "status": project_state(phase, run_status),
                    "progress": progress(phase, run_status),
                    "started": "09/11/1988",
                    "reviewed": "1/1/26 - claude: looks promising.",
                    "task": "20/80",
                    "profile_and_notes": task.get("summary", ""),
                }
            ],
        },
        "fleet_tasks": {
            "state": "Ongoing" if active else "Halted",
            "active": active,
            "total": 1,
            "tasks": [
                {
                    "id": 1,
                    "project": "Coach",
                    "name": "Testing round #1 - summarizing rules and guideset received from project manager to compare with code received from ollama worker",
                    "current_worker": task.get("next_worker") or "Resting",
                    "budget": "150k used out of 1M Tokens total between breaks of 24 hours.",
                    "status": project_state(phase, run_status),
                    "progress": progress(phase, run_status),
                    "started": "09/11/1988",
                    "reviewed": "1/1/26 - claude: looks promising. 3/1/26 - codex: tends to miss objects",
                    "task": "20/80",
                    "profile_and_notes": task.get("summary", ""),
                    "latest_run_id": latest["run_id"] if latest else None,
                }
            ],
        },
    }


def staff_state(phase: str, run_status: str) -> str:
    if run_status == "done":
        return "Resting"
    if run_status == "failed":
        return "Testing"
    return {"planning": "Planning", "building": "Coding", "reviewing": "Testing", "documenting": "Planning"}.get(phase, "Resting")


def project_state(phase: str, run_status: str) -> str:
    if run_status == "done":
        return "done"
    if run_status == "failed":
        return "awaiting review"
    return {"planning": "awaiting review", "building": "coding", "reviewing": "In testing", "documenting": "awaiting review"}.get(phase, "awaiting review")


def progress(phase: str, run_status: str) -> str:
    if run_status == "done":
        return "100/100"
    if run_status == "failed":
        return "60/100"
    return {"planning": "20/100", "building": "50/100", "reviewing": "75/100", "documenting": "90/100"}.get(phase, "0/100")
