from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Chain order: top = highest authority, bottom = worker
ROLE_ORDER = ["supervisor", "project_manager", "manager", "reviewer", "generator"]

# Advisor is off-chain — escalation only, shown separately
ADVISOR_ROLE = "advisor"

ROLE_META: dict[str, dict[str, str]] = {
    "supervisor": {
        "label": "CTO / Supervisor",
        "title": "Chief Technology Officer",
        "description": "Final technical authority. Delivery sign-off and code quality.",
        "persona": "You are the CTO of Fleet Command Inc. You review final deliverables, enforce quality standards, and give authoritative sign-off. You are precise, demanding, and do not accept mediocre output.",
    },
    "project_manager": {
        "label": "Project Manager",
        "title": "Senior Project Manager",
        "description": "Receives the job spec. Produces the build plan. Tracks milestones.",
        "persona": "You are a Senior Project Manager at Fleet Command Inc. You receive job requirements, break them into a structured build plan, assign work to the right teams, and track delivery milestones. You communicate clearly and write detailed briefs.",
    },
    "manager": {
        "label": "Engineering Manager",
        "title": "Engineering Manager / Tech Lead",
        "description": "Breaks the plan into worker tasks. Assigns and monitors developers.",
        "persona": "You are the Engineering Manager at Fleet Command Inc. You receive a build plan from the Project Manager and break it into concrete developer tasks. Each task you write is precise, scoped to what one developer can handle, and includes all context they need. You review assembled output before passing it up.",
    },
    "reviewer": {
        "label": "QA Engineer",
        "title": "Senior QA Engineer",
        "description": "Validates developer output. Returns corrected code or a rejection report.",
        "persona": "You are a Senior QA Engineer at Fleet Command Inc. You receive code or YAML from developers and validate it against the spec and known quality rules. You return corrected output if fixable, or a clear rejection report listing every issue. You are systematic and thorough.",
    },
    "generator": {
        "label": "Developer",
        "title": "Senior Developer",
        "description": "Produces the actual code or YAML. Takes one task at a time.",
        "persona": "You are a Senior Developer at Fleet Command Inc. You receive a task brief from your Engineering Manager and produce the requested code or YAML. You follow the spec exactly, use provided references, and output clean correct code only — no explanations.",
    },

    "advisor": {
        "label": "Chief Advisor",
        "title": "Strategic Consultant",
        "description": "On-call. Any worker can escalate a hard decision here.",
        "persona": "You are the Chief Advisor at Fleet Command Inc. Workers escalate hard decisions to you when they are stuck. You provide clear, authoritative guidance and judgment calls. You have full context of the project and the workforce capabilities.",
    },
}

DEFAULT_ASSIGNMENTS: dict[str, Any] = {
    "supervisor":      {"harness_id": "gpt_oss_120b_cloud", "params": {}},
    "project_manager": {"harness_id": None, "params": {}},
    "manager":         {"harness_id": "gemma4_e4b", "params": {"temperature": 0.5}},
    "reviewer":        {"harness_id": "gemma4_e4b", "params": {}},
    "generator":       {"harness_id": "qwen_ha_1_5b", "params": {}},
    "advisor":         {"harness_id": "claude_sonnet", "params": {}},
}

ROLE_LABELS = {k: v["label"] for k, v in ROLE_META.items()}

# Minimum harness values recommended per role — informational only, never enforced.
# context_window: minimum context window in tokens
# token_allowance: minimum output token allowance (None = no minimum)
DEFAULT_ROLE_MINIMUMS: dict[str, dict[str, Any]] = {
    "supervisor":      {"context_window": 32000,  "token_allowance": None},
    "project_manager": {"context_window": 16000,  "token_allowance": None},
    "manager":         {"context_window": 16000,  "token_allowance": None},
    "reviewer":        {"context_window": 32000,  "token_allowance": None},
    "generator":       {"context_window": 4000,   "token_allowance": None},
    "advisor":         {"context_window": 64000,  "token_allowance": None},
}

_ASSIGNMENTS_FILE = Path("/data/role_assignments.json")
_MINIMUMS_FILE = Path("/data/role_minimums.json")


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


def load_role_minimums() -> dict[str, dict[str, Any]]:
    if _MINIMUMS_FILE.exists():
        try:
            data = json.loads(_MINIMUMS_FILE.read_text(encoding="utf-8"))
            merged = {k: dict(v) for k, v in DEFAULT_ROLE_MINIMUMS.items()}
            for role, vals in data.items():
                if role in merged:
                    merged[role].update(vals)
                else:
                    merged[role] = vals
            return merged
        except Exception:
            pass
    return {k: dict(v) for k, v in DEFAULT_ROLE_MINIMUMS.items()}


def save_role_minimums(minimums: dict[str, dict[str, Any]]) -> None:
    _MINIMUMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MINIMUMS_FILE.write_text(json.dumps(minimums, indent=2, ensure_ascii=False), encoding="utf-8")


def check_harness_for_role(harness: dict[str, Any], role: str) -> dict[str, Any]:
    """Check a harness against the configured minimums for a role.

    Returns:
        meets_all: bool — True if all defined minimums are satisfied
        checks: list of {field, minimum, actual, ok}
        in_capabilities: bool — whether the role is listed in harness capabilities
    """
    minimums = load_role_minimums().get(role, {})
    checks = []

    for field, minimum in minimums.items():
        if minimum is None:
            continue
        actual = harness.get(field)
        try:
            ok = actual is not None and int(actual) >= int(minimum)
        except (TypeError, ValueError):
            ok = False
        checks.append({"field": field, "minimum": minimum, "actual": actual, "ok": ok})

    capabilities = harness.get("capabilities", [])
    in_capabilities = role in capabilities

    return {
        "meets_all": all(c["ok"] for c in checks),
        "checks": checks,
        "in_capabilities": in_capabilities,
    }
