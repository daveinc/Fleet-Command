from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_RULES_FILE = Path("/data/pipeline_rules.json")

_DEFAULTS: dict[str, Any] = {
    "escalation_rules": [
        {
            "id": "reviewer_threshold",
            "name": "Reviewer issue threshold",
            "description": "If reviewer finds more issues than the threshold, escalate to supervisor instead of fixing inline.",
            "stage": "reviewer",
            "threshold": 5,
            "action": "escalate_to_supervisor",
            "enabled": True,
        }
    ],
    "loop_rules": [],
    "retry_rules": [],
}


def load_rules() -> dict[str, Any]:
    if _RULES_FILE.exists():
        try:
            return json.loads(_RULES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return json.loads(json.dumps(_DEFAULTS))


def save_rules(rules: dict[str, Any]) -> None:
    _RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _RULES_FILE.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")


def get_escalation_rule(rule_id: str) -> dict[str, Any] | None:
    rules = load_rules()
    for r in rules.get("escalation_rules", []):
        if r.get("id") == rule_id:
            return r
    return None


def reviewer_threshold() -> int:
    rule = get_escalation_rule("reviewer_threshold")
    if rule and rule.get("enabled"):
        return int(rule.get("threshold", 5))
    return 9999
