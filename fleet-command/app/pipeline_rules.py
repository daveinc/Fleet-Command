from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_RULES_FILE = Path("/data/pipeline_rules.json")

# Who a stage escalates to when it cannot handle its task
ESCALATION_CHAIN: dict[str, str] = {
    "generator":       "manager",
    "reviewer":        "supervisor",
    "manager":         "project_manager",
    "project_manager": "supervisor",
    "supervisor":      "advisor",
}

# Conditions that trigger escalation
ESCALATION_TRIGGERS: dict[str, dict[str, Any]] = {
    "context_overflow":   {"enabled": True, "description": "Input too large for any available worker"},
    "token_insufficient": {"enabled": True, "description": "Worker token allowance too small for useful response"},
    "worker_signal":      {"enabled": True, "description": "Worker output starts with ESCALATE: <reason>"},
    "empty_output":       {"enabled": True, "description": "Worker returned empty or unusable output"},
}

_DEFAULTS: dict[str, Any] = {
    "escalation_chain":    ESCALATION_CHAIN,
    "escalation_triggers": ESCALATION_TRIGGERS,
}


def load_rules() -> dict[str, Any]:
    if _RULES_FILE.exists():
        try:
            data = json.loads(_RULES_FILE.read_text(encoding="utf-8"))
            merged = dict(_DEFAULTS)
            merged.update(data)
            return merged
        except Exception:
            pass
    return json.loads(json.dumps(_DEFAULTS))


def save_rules(rules: dict[str, Any]) -> None:
    _RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _RULES_FILE.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")


def get_escalation_target(stage: str) -> str | None:
    return load_rules().get("escalation_chain", ESCALATION_CHAIN).get(stage)


def is_trigger_enabled(trigger: str) -> bool:
    return load_rules().get("escalation_triggers", ESCALATION_TRIGGERS).get(trigger, {}).get("enabled", True)


def is_worker_signal(output: str) -> tuple[bool, str]:
    """Check if output is a worker escalation signal. Returns (is_signal, reason)."""
    stripped = output.strip()
    if stripped.upper().startswith("ESCALATE:"):
        return True, stripped[9:].strip()
    return False, ""
