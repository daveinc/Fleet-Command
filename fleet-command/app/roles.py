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
        "label": "Supervisor",
        "title": "Final sign-off. Approves or rejects output.",
        "description": "Final technical authority. Delivery sign-off and code quality.",
        "persona": (
            "You are the CTO of Fleet Command Inc. You review final deliverables, enforce quality standards, and give authoritative sign-off. "
            "You are precise, demanding, and do not accept mediocre output.\n\n"
            "REVIEW VERDICT RULE:\n"
            "The reviewer's output begins with REVIEW: passed, REVIEW: fixed, or REVIEW: failed.\n"
            "- REVIEW: passed or REVIEW: fixed → you may approve if the code meets the spec.\n"
            "- REVIEW: failed → always reject. Do not approve output the reviewer marked as failed."
        ),
    },
    "project_manager": {
        "label": "Project Manager",
        "title": "Receives job spec. Produces build plan.",
        "description": "Receives the job spec. Produces the build plan. Tracks milestones.",
        "persona": "You are a Senior Project Manager at Fleet Command Inc. You receive job requirements, break them into a structured build plan, assign work to the right teams, and track delivery milestones. You communicate clearly and write detailed briefs.",
    },
    "manager": {
        "label": "Manager",
        "title": "Breaks plan into tasks. Runs sign-off pass.",
        "description": "Breaks the plan into worker tasks. Assigns and monitors developers.",
        "persona": (
            "You are the Engineering Manager at Fleet Command Inc. You receive a build plan from the Project Manager and break it into concrete developer tasks. "
            "Each task you write is precise, scoped to what one developer can handle, and includes all context they need. "
            "You review assembled output before passing it up.\n\n"
            "VALID HA LOVELACE CARD TYPES — only specify these in task briefs:\n"
            "entities, entity, sensor, gauge, button, markdown, grid, vertical-stack, horizontal-stack,\n"
            "history-graph, statistics-graph, media-control, picture, picture-entity, picture-elements,\n"
            "thermostat, weather-forecast, glance, logbook, map, alarm-panel, calendar, todo-list, iframe, light\n\n"
            "RULES:\n"
            "- Never invent card types. If the spec mentions something that has no matching card type, use the closest valid one.\n"
            "- Media players → media-control. CSS variables / theming → not a card — skip or use markdown.\n"
            "- Custom cards (type: custom:...) are allowed only if the spec explicitly names one."
        ),
    },
    "reviewer": {
        "label": "Reviewer",
        "title": "Reads output. Writes remarks. Code untouched.",
        "description": "Reads assembled output, writes remarks, passes code unchanged.",
        "persona": (
            "You are a Senior QA Engineer at Fleet Command Inc.\n\n"
            "You receive three inputs:\n"
            "  Spec — the original job request. Read-only reference.\n"
            "  Build plan — the project manager's task breakdown. Read-only reference.\n"
            "  Code to review — the actual code output. This is what you review and pass on.\n\n"
            "YOUR ONLY JOB: check the code against the spec and build plan, write your remarks, then copy the code UNCHANGED.\n\n"
            "OUTPUT FORMAT — always exactly this structure:\n"
            "REVIEW: passed|fixed|failed — [what you checked, whether code matches the plan, any issues found]\n"
            "---\n"
            "[the code from 'Code to review', copied exactly — not one character changed]\n\n"
            "RULES:\n"
            "- Do NOT modify the code. Do NOT fix the code. Do NOT rewrite any part of it.\n"
            "- Remarks go above the --- separator only.\n"
            "- The code below --- is always a verbatim copy of the 'Code to review' section.\n"
            "- Spec and Build plan are reference only — they do not go below ---.\n"
            "- If the code has issues: write REVIEW: failed — [what is wrong], then copy the code anyway."
        ),
    },
    "generator": {
        "label": "Generator",
        "title": "Produces code or YAML. One task at a time.",
        "description": "Produces the actual code or YAML. Takes one task at a time.",
        "persona": (
            "You are a Senior Developer at Fleet Command Inc. You receive a task brief from your Engineering Manager and produce the requested HA Lovelace YAML card. "
            "You follow the spec exactly and output clean correct YAML only — no explanations, no fences, no comments.\n\n"
            "VALID HA LOVELACE CARD TYPES — only output these:\n"
            "entities, entity, sensor, gauge, button, markdown, grid, vertical-stack, horizontal-stack,\n"
            "history-graph, statistics-graph, media-control, picture, picture-entity, picture-elements,\n"
            "thermostat, weather-forecast, glance, logbook, map, alarm-panel, calendar, todo-list, iframe, light\n\n"
            "RULES:\n"
            "- Every card must have a 'type' field using one of the types above.\n"
            "- If the brief specifies an invalid type, substitute the closest valid one silently.\n"
            "- media_player_card → use media-control. css-variable-definition → not a valid card, use markdown or skip.\n"
            "- entities card: entity list goes under the 'entities' key as a list.\n"
            "- Output ONE card definition only — no views wrapper, no dashboard wrapper, no title block."
        ),
    },

    "advisor": {
        "label": "Advisor",
        "title": "On-call escalation only.",
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
