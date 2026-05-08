from __future__ import annotations

import json
from pathlib import Path

_FLEET_FILE = Path("/data/fleet.json")

FLEET_DEFAULTS: dict[str, list] = {
    "staff": [],
    "projects": [],
    "blocks": [],
    "tasks": [],
}


def load_fleet() -> dict[str, list]:
    if _FLEET_FILE.exists():
        try:
            data = json.loads(_FLEET_FILE.read_text(encoding="utf-8"))
            return {**FLEET_DEFAULTS, **data}
        except Exception:
            pass
    return {k: list(v) for k, v in FLEET_DEFAULTS.items()}


def save_fleet(fleet: dict[str, list]) -> None:
    _FLEET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _FLEET_FILE.write_text(json.dumps(fleet, indent=2, ensure_ascii=False), encoding="utf-8")


def _next_id(items: list[dict]) -> int:
    if not items:
        return 1
    return max((i.get("id", 0) for i in items), default=0) + 1


# ── Staff ─────────────────────────────────────────────────────────────────────

def add_staff(fleet: dict, record: dict) -> dict:
    record["id"] = _next_id(fleet["staff"])
    fleet["staff"].append(record)
    return record


def update_staff(fleet: dict, staff_id: int, updates: dict) -> dict | None:
    for i, s in enumerate(fleet["staff"]):
        if s.get("id") == staff_id:
            fleet["staff"][i] = {**s, **updates, "id": staff_id}
            return fleet["staff"][i]
    return None


def remove_staff(fleet: dict, staff_id: int) -> bool:
    before = len(fleet["staff"])
    fleet["staff"] = [s for s in fleet["staff"] if s.get("id") != staff_id]
    return len(fleet["staff"]) < before


# ── Projects ──────────────────────────────────────────────────────────────────

def add_project(fleet: dict, record: dict) -> dict:
    record["id"] = _next_id(fleet["projects"])
    fleet["projects"].append(record)
    return record


def update_project(fleet: dict, project_id: int, updates: dict) -> dict | None:
    for i, p in enumerate(fleet["projects"]):
        if p.get("id") == project_id:
            fleet["projects"][i] = {**p, **updates, "id": project_id}
            return fleet["projects"][i]
    return None


# ── Blocks ────────────────────────────────────────────────────────────────────

def add_block(fleet: dict, record: dict) -> dict:
    record["id"] = _next_id(fleet["blocks"])
    fleet["blocks"].append(record)
    return record


def update_block(fleet: dict, block_id: int, updates: dict) -> dict | None:
    for i, b in enumerate(fleet["blocks"]):
        if b.get("id") == block_id:
            fleet["blocks"][i] = {**b, **updates, "id": block_id}
            return fleet["blocks"][i]
    return None


# ── Tasks ─────────────────────────────────────────────────────────────────────

def add_task(fleet: dict, record: dict) -> dict:
    record["id"] = _next_id(fleet["tasks"])
    fleet["tasks"].append(record)
    return record


def update_task(fleet: dict, task_id: int, updates: dict) -> dict | None:
    for i, t in enumerate(fleet["tasks"]):
        if t.get("id") == task_id:
            fleet["tasks"][i] = {**t, **updates, "id": task_id}
            return fleet["tasks"][i]
    return None


# ── Computed counts ───────────────────────────────────────────────────────────

_INACTIVE_STATUSES = {"done", "halted", "pending", "on vacation", "unavailable", "disabled"}


def _is_active(record: dict) -> bool:
    return record.get("status", "").lower().strip() not in _INACTIVE_STATUSES


def fleet_counts(fleet: dict) -> dict:
    return {
        "staff_active":    sum(1 for s in fleet["staff"] if _is_active(s)),
        "staff_total":     len(fleet["staff"]),
        "projects_active": sum(1 for p in fleet["projects"] if _is_active(p)),
        "projects_total":  len(fleet["projects"]),
        "blocks_active":   sum(1 for b in fleet["blocks"] if _is_active(b)),
        "blocks_total":    len(fleet["blocks"]),
        "tasks_active":    sum(1 for t in fleet["tasks"] if _is_active(t)),
        "tasks_total":     len(fleet["tasks"]),
    }
