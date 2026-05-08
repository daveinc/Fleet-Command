from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROLE_ORDER = ["project_manager", "manager", "generator", "reviewer", "supervisor"]

ROLE_LABELS = {
    "project_manager": "Project Manager",
    "manager": "Manager",
    "generator": "Generator",
    "reviewer": "Reviewer",
    "supervisor": "Supervisor",
}

DEFAULT_ASSIGNMENTS: dict[str, Any] = {
    "project_manager": {"harness_id": None, "params": {}},
    "manager":         {"harness_id": "gemma4_e4b", "params": {"temperature": 0.5}},
    "generator":       {"harness_id": "qwen_ha_1_5b", "params": {}},
    "reviewer":        {"harness_id": "gemma4_e4b", "params": {}},
    "supervisor":      {"harness_id": "gpt_oss_120b_cloud", "params": {}},
}

_ASSIGNMENTS_FILE = Path("/data/role_assignments.json")


def load_roles() -> dict[str, Any]:
    if _ASSIGNMENTS_FILE.exists():
        try:
            data = json.loads(_ASSIGNMENTS_FILE.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_ASSIGNMENTS)
            merged.update(data)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_ASSIGNMENTS)


def save_roles(assignments: dict[str, Any]) -> None:
    _ASSIGNMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ASSIGNMENTS_FILE.write_text(
        json.dumps(assignments, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def swap_roles(assignments: dict[str, Any], role_a: str, role_b: str) -> dict[str, Any]:
    result = dict(assignments)
    result[role_a], result[role_b] = result[role_b], result[role_a]
    return result
