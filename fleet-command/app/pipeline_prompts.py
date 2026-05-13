from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PROMPTS_FILE = Path("/data/pipeline_prompts.json")

# Variables available per stage: shown in UI as hints
PROMPT_VARIABLES: dict[str, list[str]] = {
    "project_manager": ["{spec}"],
    "manager":         ["{spec}", "{prev}"],
    "generator":       ["{spec}", "{task}", "{block}", "{prev}"],
    "assembler":       ["{spec}", "{fragments}"],
    "reviewer":        ["{spec}", "{prev}"],
    "supervisor":      ["{spec}", "{prev}"],
}

DEFAULT_PROMPTS: dict[str, str] = {
    "project_manager": (
        "Job request: {spec}\n\n"
        "Max tasks per run: {max_tasks}\n\n"
        "If total card count fits within {max_tasks} tasks: list the major UI blocks and what cards each contains. Plain text only. No YAML. Under 100 words.\n"
        "If total card count exceeds {max_tasks} tasks: split into sub-jobs, each under {max_tasks} tasks. Output only:\n"
        "SUB-JOB 1: [self-contained scope description]\n"
        "SUB-JOB 2: [self-contained scope description]\n"
        "One line per sub-job. No extra text. No YAML."
    ),
    "manager": (
        "Original request: {spec}\n\n"
        "Plan:\n{prev}\n\n"
        "Output ONLY a block/task list in this exact format. Nothing else:\n\n"
        "BLOCK 1: [name]\n"
        "- Task 1: [card type] [brief purpose]\n"
        "- Task 2: [card type] [brief purpose]\n\n"
        "BLOCK 2: [name]\n"
        "- Task 1: ...\n\n"
        "Rules: one task = one card. Match the exact card count in the original request. No YAML. No explanations. No intro text."
    ),
    "generator": (
        "Task: {task}\n"
        "Block: {block}\n\n"
        "Output ONE valid Home Assistant Lovelace card in YAML. Card definition only — no views, no title, no dashboard wrapper.\n\n"
        "Card examples:\n"
        "  Static markdown:\n"
        "    type: markdown\n"
        "    content: |\n"
        "      ## Title\n"
        "      Your text here.\n"
        "  Markdown with live template:\n"
        "    type: markdown\n"
        "    content: |\n"
        "      Current time: {{{{ now().strftime('%H:%M') }}}}\n"
        "      Value: {{{{ states('sensor.example') }}}}\n"
        "  Entities:\n"
        "    type: entities\n"
        "    title: Title\n"
        "    entities:\n"
        "      - entity: sensor.example\n\n"
        "Rules:\n"
        "- Placeholders (tip of the day, welcome text, etc.) use plain static text — no templates\n"
        "- Live sensor data uses {{{{ states('sensor.x') }}}} inside content:\n"
        "- Current date/time uses {{{{ now().strftime('%Y-%m-%d %H:%M') }}}} inside content:\n"
        "- Never output sensor: definitions — Lovelace cards only\n\n"
        "YAML only. No explanation. No fences."
    ),
    "generator_single": (
        "Build this: {spec}\n\n"
        "Output complete valid Home Assistant Lovelace YAML. Include title, views, and all cards.\n"
        "YAML only. No explanation. No fences."
    ),
    "assembler": (
        "Job specification: {spec}\n\n"
        "Card fragments:\n{fragments}\n\n"
        "Combine into one complete Home Assistant Lovelace dashboard YAML.\n"
        "Structure:\n"
        "  title: Dashboard Title\n"
        "  views:\n"
        "    - title: View Name\n"
        "      cards:\n"
        "        - [card here]\n\n"
        "Group cards under views by their block name. Fix any invalid card fields (e.g. markdown cards must use 'content:', not 'entity:').\n"
        "Output complete YAML only. No explanations. No fences."
    ),
    "reviewer": (
        "Spec: {spec}\n\n"
        "YAML to review:\n{prev}\n\n"
        "Fix any issues you find directly — do not just list them. Output the corrected YAML.\n"
        "Issues to fix: markdown code fences (``` lines) anywhere in the YAML, invalid card types, "
        "markdown cards using 'entity:' instead of 'content:', sensor definitions inside views, missing 'type:' fields.\n"
        "First line MUST be: '# REVIEW: <one-sentence verdict>'\n"
        "Examples: '# REVIEW: Fixed code fences and corrected 2 card types.' or '# REVIEW: Valid — no changes needed.'\n"
        "Then output the complete corrected YAML. YAML only after the review line. No fences. No explanations."
    ),
    "supervisor": (
        "Job specification:\n{spec}\n\n"
        "Final output for sign-off:\n{prev}\n\n"
        "If the YAML is a valid Home Assistant Lovelace dashboard that fulfils the spec, return it unchanged.\n"
        "If not, output exactly three lines:\n"
        "REJECTED_AT: <stage>\n"
        "REJECTED: <specific reason what is wrong>\n"
        "CORRECTIVE_BRIEF: <exact instructions for that stage to fix the problem — card types to use, entities, structure, what to avoid — be specific enough that the worker can act on it without asking questions>\n"
        "Valid REJECTED_AT stages: project_manager, manager, generator, reviewer.\n"
        "Choose the stage closest to where the error originated.\n"
        "Plain text only — no markdown, no bold, no bullet symbols."
    ),
}


def load_prompts() -> dict[str, str]:
    if _PROMPTS_FILE.exists():
        try:
            overrides = json.loads(_PROMPTS_FILE.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_PROMPTS)
            merged.update(overrides)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_PROMPTS)


def save_prompts(overrides: dict[str, str]) -> None:
    _PROMPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PROMPTS_FILE.write_text(
        json.dumps(overrides, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def render_prompt(stage: str, **kwargs: Any) -> str:
    prompts = load_prompts()
    template = prompts.get(stage, "{spec}")
    if stage == "project_manager" and "max_tasks" not in kwargs:
        from app.pipeline_rules import get_max_tasks_per_run
        kwargs["max_tasks"] = get_max_tasks_per_run()
    try:
        return template.format(**kwargs)
    except KeyError:
        return template


# ── Modelfile management ─────────────────────────────────────────────────────

_MODELFILES_FILE = Path("/data/pipeline_modelfiles.json")


def load_modelfiles() -> dict[str, Any]:
    if _MODELFILES_FILE.exists():
        try:
            return json.loads(_MODELFILES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_modelfiles(data: dict[str, Any]) -> None:
    _MODELFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MODELFILES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def save_modelfile(harness_id: str, content: str) -> None:
    data = load_modelfiles()
    entry = data.get(harness_id, {})
    entry["content"] = content
    entry["pushed"] = False
    data[harness_id] = entry
    _save_modelfiles(data)


def is_modelfile_pushed(harness_id: str) -> bool:
    return bool(load_modelfiles().get(harness_id, {}).get("pushed", False))


def mark_modelfile_pushed(harness_id: str) -> None:
    from datetime import datetime, timezone
    data = load_modelfiles()
    entry = data.get(harness_id, {})
    entry["pushed"] = True
    entry["pushed_at"] = datetime.now(timezone.utc).isoformat()
    data[harness_id] = entry
    _save_modelfiles(data)


async def fetch_modelfile_from_ollama(model: str, ollama_host: str) -> str | None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{ollama_host}/api/show", json={"name": model})
            resp.raise_for_status()
            data = resp.json()
            return data.get("modelfile") or data.get("Modelfile")
    except Exception:
        return None


def _merge_modelfile(
    existing: str,
    new_system: str,
    new_params: dict[str, Any],
    new_messages: list[tuple[str, str]],
) -> str:
    """Merge generated SYSTEM, PARAMETERs, and MESSAGE examples into an existing Modelfile.
    Preserves FROM, TEMPLATE, ADAPTER lines. Strips LICENSE block entirely.
    Replaces SYSTEM block, our PARAMETER lines, and all MESSAGE lines.
    """
    our_param_keys = {k.lower() for k in new_params}
    lines_out: list[str] = []
    in_system = False
    in_license = False
    system_written = False
    params_written: set[str] = set()

    for line in existing.splitlines():
        stripped = line.strip()
        upper = stripped.upper()

        # Skip all existing MESSAGE lines — we replace them entirely
        if upper.startswith("MESSAGE "):
            continue

        # Detect start of existing SYSTEM block
        if upper.startswith("SYSTEM"):
            in_system = True
            if not system_written:
                lines_out.append(f'SYSTEM """\n{new_system}\n"""')
                system_written = True
            if '"""' in stripped[6:]:
                in_system = False
            continue

        if in_system:
            if '"""' in stripped:
                in_system = False
                # Line that closes SYSTEM might also open another block (e.g. LICENSE """)
                if upper.startswith("LICENSE"):
                    in_license = True
            continue

        # Strip LICENSE block entirely — opening line + all body until closing """
        if upper.startswith("LICENSE"):
            in_license = True
            if stripped.count('"""') >= 2:
                in_license = False  # single-line LICENSE block
            continue

        if in_license:
            if '"""' in stripped:
                in_license = False
            continue

        # Replace our PARAMETER lines
        if upper.startswith("PARAMETER"):
            parts = stripped.split(None, 2)
            if len(parts) >= 2:
                key = parts[1].lower()
                if key in our_param_keys:
                    if key not in params_written:
                        lines_out.append(f"PARAMETER {parts[1]} {new_params[parts[1]]}")
                        params_written.add(key)
                    continue
        lines_out.append(line)

    if not system_written:
        lines_out.append(f'\nSYSTEM """\n{new_system}\n"""')

    for key, val in new_params.items():
        if key.lower() not in params_written:
            lines_out.append(f"PARAMETER {key} {val}")

    # Append MESSAGE examples using """ delimiters (Ollama multi-line format)
    if new_messages:
        lines_out.append("")
        for role_tag, msg in new_messages:
            lines_out.append(f'MESSAGE {role_tag} """\n{msg}\n"""')

    return "\n".join(lines_out)


# ── Per-role default parameters ───────────────────────────────────────────────

_ROLE_PARAMS: dict[str, dict[str, Any]] = {
    "generator": {
        "repeat_penalty": 1.15,
        "repeat_last_n": 128,
        "top_k": 20,
        "top_p": 0.85,
    },
    "reviewer": {
        "repeat_penalty": 1.05,
        "top_k": 30,
        "top_p": 0.9,
    },
    "manager": {
        "repeat_penalty": 1.1,
        "top_k": 30,
        "top_p": 0.9,
    },
    "project_manager": {
        "repeat_penalty": 1.1,
        "top_k": 40,
        "top_p": 0.95,
    },
    "supervisor": {
        "repeat_penalty": 1.05,
        "top_k": 40,
        "top_p": 0.95,
    },
    "advisor": {
        "repeat_penalty": 1.05,
        "top_k": 40,
        "top_p": 0.95,
    },
}

# Stop sequences disabled — Ollama newer versions reject multiple PARAMETER stop lines
# (error: "option stop must be of type array"). Left empty until Ollama fix or array format confirmed.
_ROLE_STOPS: dict[str, list[str]] = {}


# Context window threshold below which we use minimal (1-example) few-shot
_SMALL_MODEL_CTX = 32768

# Available output types — determines which MESSAGE examples are baked into modelfiles
OUTPUT_TYPES: list[str] = ["yaml", "python"]


def get_output_types() -> list[str]:
    return OUTPUT_TYPES


# ── Per-role MESSAGE examples — minimal tier (small/1.5b models) ──────────────
# Keyed by output type → role → list of (role_tag, message) pairs

_ROLE_MESSAGES_MINIMAL_YAML: dict[str, list[tuple[str, str]]] = {
    "generator": [
        (
            "user",
            "I need a card built — here's the task brief and block assignment.",
        ),
        (
            "assistant",
            "Sure — *yaml card output here, card definition only*",
        ),
        (
            "user",
            "I need a card built:\nTask: [card type]\nBlock: [block name]\n\nAdditional instructions: [corrective brief — card types, entities, what to avoid]",
        ),
        (
            "assistant",
            "*yaml card output here — following corrective instructions exactly*",
        ),
    ],
    "manager": [
        (
            "user",
            "I need a block/task breakdown — here's the spec and the PM plan.",
        ),
        (
            "assistant",
            "BLOCK 1: [name]\n- Task 1: [card type and purpose]\n- Task 2: [card type and purpose]",
        ),
        (
            "user",
            "I need a block/task breakdown.\n\nAdditional instructions: [corrective brief — how to restructure blocks or tasks]",
        ),
        (
            "assistant",
            "BLOCK 1: [restructured name]\n- Task 1: [adjusted card type per brief]",
        ),
    ],
    "project_manager": [
        (
            "user",
            "I need a job scoped — here's the spec and the task limit.",
        ),
        (
            "assistant",
            "Block 1: [domain]\n- [card type]\n\nBlock 2: [domain]\n- [card type]",
        ),
        (
            "user",
            "I need a job scoped.\n\nAdditional instructions: [corrective brief — scope constraints, blocks to simplify]",
        ),
        (
            "assistant",
            "Block 1: [rescoped domain per brief]\n- [card type per constraints]",
        ),
    ],
    "reviewer": [
        (
            "user",
            "I need this reviewed — here's the spec and the assembled output. *expect yaml code here*",
        ),
        (
            "assistant",
            "# REVIEW: [verdict — fixed X / valid / escalating]\n*corrected yaml output here*",
        ),
        (
            "user",
            "I need this reviewed. *expect yaml code here*\n\nAdditional instructions: [corrective brief — specific issues to look for and fix]",
        ),
        (
            "assistant",
            "# REVIEW: Applied corrective brief — [what was fixed].\n*full corrected yaml here*",
        ),
    ],
    "supervisor": [
        (
            "user",
            "I need a final sign-off — here's the spec and the reviewed output. *expect yaml code here*",
        ),
        (
            "assistant",
            "*pass: yaml returned unchanged* or REJECTED_AT: [stage]\nREJECTED: [specific reason]\nCORRECTIVE_BRIEF: [exact fix instructions for that stage]",
        ),
    ],
    "advisor": [
        (
            "user",
            "A supervisor worker is escalating to you — it cannot complete its task.\n\nReason: [escalation reason]\n\nTask context:\n[job summary]\n\nProvide clear, specific guidance.",
        ),
        (
            "assistant",
            "[direct actionable guidance — break loop / substitute / accept partial / flag for user]",
        ),
    ],
}

# ── Per-role MESSAGE examples (few-shot, yaml output type) ────────────────────

_ROLE_MESSAGES_YAML: dict[str, list[tuple[str, str]]] = {
    "generator": [
        # Normal task → output card YAML only
        (
            "user",
            "I need a card built — here's the task brief and block assignment:\n"
            "Task: [card type and purpose]\n"
            "Block: [block name]",
        ),
        (
            "assistant",
            "Sure — *yaml card output here, card definition only*",
        ),
        # Missing entity → escalate, don't guess
        (
            "user",
            "I need a card built — here's the task:\n"
            "Task: [card requiring entity ID that was not provided]\n"
            "Block: [block name]",
        ),
        (
            "assistant",
            "ESCALATE: missing entity — [entity purpose] entity ID not provided, cannot build card without it",
        ),
        # Corrective brief injected after supervisor rejection
        (
            "user",
            "I need a card built:\n"
            "Task: [card type]\n"
            "Block: [block name]\n\n"
            "Additional instructions: [corrective brief from supervisor — specific card types, entity IDs, structure to use, what to avoid]",
        ),
        (
            "assistant",
            "*yaml card output here — following all corrective instructions exactly*",
        ),
        # Retry after rejection
        (
            "user",
            "I need a card rebuilt — previous attempt was rejected:\n"
            "Task: [card type]\n"
            "Block: [block name]\n\n"
            "This is a retry. The original request was:\n"
            "[original task spec]\n\n"
            "The previous attempt was rejected with these remarks:\n"
            "[rejection reason]\n\n"
            "Address all rejection remarks in your output.",
        ),
        (
            "assistant",
            "*corrected yaml card here — all rejection remarks addressed*",
        ),
    ],
    "manager": [
        # PM plan → structured BLOCK/task breakdown
        (
            "user",
            "I need a block/task breakdown — here's the spec and the PM plan:\n"
            "Original request: [job description]\n\n"
            "Plan:\n"
            "[PM block summary]",
        ),
        (
            "assistant",
            "BLOCK 1: [name]\n"
            "- Task 1: [card type and purpose]\n"
            "- Task 2: [card type and purpose]\n\n"
            "BLOCK 2: [name]\n"
            "- Task 1: [card type and purpose]",
        ),
        # Corrective brief from supervisor — restructure failing block/tasks
        (
            "user",
            "I need a block/task breakdown — here's the spec:\n"
            "Original request: [job description]\n\n"
            "Plan:\n"
            "[PM block summary]\n\n"
            "Additional instructions: [corrective brief from supervisor — how to restructure blocks or tasks to fix the failure]",
        ),
        (
            "assistant",
            "BLOCK 1: [restructured name per corrective brief]\n"
            "- Task 1: [adjusted card type and purpose]\n"
            "- Task 2: [adjusted card type and purpose]",
        ),
        # Generator escalated with missing entity
        (
            "user",
            "A generator worker is escalating to you — it cannot complete its task.\n\n"
            "Reason: [escalation reason from generator]\n\n"
            "Task context:\n"
            "[task brief that was being worked on]\n\n"
            "Provide clear, specific guidance on how to proceed.",
        ),
        (
            "assistant",
            "[direct fix — supply missing info, correct the task, or specify a different approach]",
        ),
        # Plan too large → escalate back to PM
        (
            "user",
            "I need a block/task breakdown — here's the spec:\n"
            "Original request: [very large multi-domain job]\n\n"
            "Plan:\n"
            "[PM plan with too many blocks / tasks to break down in one pass]",
        ),
        (
            "assistant",
            "ESCALATE: plan too large — [N] blocks and [M]+ tasks exceeds single breakdown pass, recommend PM splits into separate sub-jobs per domain",
        ),
    ],
    "project_manager": [
        # Normal spec that fits within threshold → block list
        (
            "user",
            "I need a job scoped — here's the spec and task limit:\n"
            "Job request: [job description that fits within max_tasks]\n\n"
            "Max tasks per run: [limit]",
        ),
        (
            "assistant",
            "Block 1: [domain]\n"
            "- [card type]\n"
            "- [card type]\n\n"
            "Block 2: [domain]\n"
            "- [card type]",
        ),
        # Corrective brief from supervisor — re-scope with specific constraints
        (
            "user",
            "I need a job scoped — here's the spec:\n"
            "Job request: [job description]\n\n"
            "Max tasks per run: [limit]\n\n"
            "Additional instructions: [corrective brief from supervisor — specific constraints on scope, blocks to remove or simplify, approach to take]",
        ),
        (
            "assistant",
            "Block 1: [rescoped domain per corrective brief]\n"
            "- [card type per constraints]\n\n"
            "Block 2: [rescoped domain]\n"
            "- [card type per constraints]",
        ),
        # Scope exceeds threshold → split into sub-jobs
        (
            "user",
            "I need a job scoped — here's the spec:\n"
            "Job request: [multi-domain job that exceeds max_tasks]\n\n"
            "Max tasks per run: [limit]",
        ),
        (
            "assistant",
            "SUB-JOB 1: [self-contained domain scope]\n"
            "SUB-JOB 2: [self-contained domain scope]\n"
            "SUB-JOB 3: [self-contained domain scope]",
        ),
        # Single domain genuinely too large even after splitting → escalate
        (
            "user",
            "I need a job scoped — here's the spec:\n"
            "Job request: [single domain so large it cannot be split into runs under the task limit]\n\n"
            "Max tasks per run: [limit]",
        ),
        (
            "assistant",
            "ESCALATE: single domain exceeds run limit — [reason] cannot be split into sub-jobs each under [limit] tasks without losing coherence, requires manual scoping",
        ),
    ],
    "reviewer": [
        # Valid assembled dashboard → pass through unchanged
        (
            "user",
            "I need this reviewed — here's the spec and the assembled output.\n"
            "Spec: [job description]\n\n"
            "YAML to review:\n"
            "*assembled dashboard yaml here — N blocks, M cards across K views*",
        ),
        (
            "assistant",
            "# REVIEW: Valid — no changes needed.\n"
            "*full yaml returned unchanged here*",
        ),
        # Assembled dashboard with fixable issues
        (
            "user",
            "I need this reviewed — assembled output has issues.\n"
            "Spec: [job description]\n\n"
            "YAML to review:\n"
            "*assembled dashboard yaml here — contains invalid fields, leaked fences, or wrong card structure*",
        ),
        (
            "assistant",
            "# REVIEW: Fixed [N] issues — [brief description of what was corrected].\n"
            "*full corrected yaml here*",
        ),
        # Corrective brief injected — focus review on specific failure areas
        (
            "user",
            "I need this reviewed:\n"
            "Spec: [job description]\n\n"
            "YAML to review:\n"
            "*assembled dashboard yaml here*\n\n"
            "Additional instructions: [corrective brief from supervisor — specific issues to look for and fix, card types or fields to validate]",
        ),
        (
            "assistant",
            "# REVIEW: Applied corrective brief — [what was fixed per instructions].\n"
            "*full corrected yaml here*",
        ),
        # Input too large for single pass
        (
            "user",
            "I need this reviewed:\n"
            "Spec: [job description]\n\n"
            "YAML to review:\n"
            "*assembled dashboard yaml here — very large, exceeds context capacity*",
        ),
        (
            "assistant",
            "ESCALATE: context too large — [N]-card dashboard cannot be reviewed in a single pass, chunking required",
        ),
    ],
    "supervisor": [
        # Valid output matching spec → return unchanged
        (
            "user",
            "I need a final sign-off — here's the spec and the reviewed output.\n"
            "Job specification:\n"
            "[job description]\n\n"
            "Final output for sign-off:\n"
            "*reviewed dashboard yaml here — complete, all views and cards*",
        ),
        (
            "assistant",
            "*full yaml returned unchanged — output approved*",
        ),
        # Output doesn't fulfil spec → reject with stage, reason, and corrective brief
        (
            "user",
            "I need a final sign-off — output has problems.\n"
            "Job specification:\n"
            "[job description]\n\n"
            "Final output for sign-off:\n"
            "*reviewed dashboard yaml here — contains an error the reviewer missed*",
        ),
        (
            "assistant",
            "REJECTED_AT: [stage closest to where the error originated]\n"
            "REJECTED: [specific reason — what is wrong]\n"
            "CORRECTIVE_BRIEF: [exact fix instructions for that stage — card types, entity IDs, structure, what to avoid — specific enough to act on without questions]",
        ),
    ],
    "advisor": [
        # Supervisor cannot make final call on partially valid output
        (
            "user",
            "A supervisor worker is escalating to you — it cannot complete its task.\n\n"
            "Reason: output is partially valid — 3 of 5 views are correct YAML but 2 have unresolvable entity references that reviewer could not fix\n\n"
            "Task context:\n"
            "Job: Weather and security dashboard — output has been through 2 reviewer passes, still has 2 cards with unknown entities\n\n"
            "Provide clear, specific guidance on how to proceed. You may: clarify the task, provide missing information, split it differently, or specify a different approach. Be concise and actionable. Your response will be passed directly back to the worker.",
        ),
        (
            "assistant",
            "Accept the 3 valid views and reject the 2 with unresolvable entities. "
            "Output REJECTED_AT: generator with specific entity IDs that need user clarification before rerunning. "
            "Do not block the whole job on 2 cards — flag them and pass the rest.",
        ),
        # Supervisor stuck in retry loop → break it
        (
            "user",
            "A supervisor worker is escalating to you — it cannot complete its task.\n\n"
            "Reason: job has been rejected 3 times at generator stage — same entity resolution failure each pass\n\n"
            "Task context:\n"
            "Original spec: Solar monitoring dashboard — sensor.solar_power not found (×3)\n\n"
            "Provide clear, specific guidance on how to proceed. You may: clarify the task, provide missing information, split it differently, or specify a different approach. Be concise and actionable. Your response will be passed directly back to the worker.",
        ),
        (
            "assistant",
            "Break the loop — generator cannot guess entity IDs from spec alone. "
            "Accept what was produced, replacing the unknown sensor card with a static markdown card noting that solar entity requires user configuration. "
            "Return the job as complete with that substitution.",
        ),
    ],
}

# ── Python output type — MESSAGE examples (placeholder, TBD) ──────────────────
_ROLE_MESSAGES_PYTHON: dict[str, list[tuple[str, str]]] = {}
_ROLE_MESSAGES_MINIMAL_PYTHON: dict[str, list[tuple[str, str]]] = {}

# ── Output type registry — add new types here ─────────────────────────────────
_MESSAGES_BY_TYPE: dict[str, dict[str, list[tuple[str, str]]]] = {
    "yaml":   _ROLE_MESSAGES_YAML,
    "python": _ROLE_MESSAGES_PYTHON,
}
_MESSAGES_MINIMAL_BY_TYPE: dict[str, dict[str, list[tuple[str, str]]]] = {
    "yaml":   _ROLE_MESSAGES_MINIMAL_YAML,
    "python": _ROLE_MESSAGES_MINIMAL_PYTHON,
}


def _get_messages(role: str, ctx_window: int, output_type: str) -> list[tuple[str, str]]:
    t = output_type if output_type in _MESSAGES_BY_TYPE else "yaml"
    if ctx_window and ctx_window <= _SMALL_MODEL_CTX:
        return _MESSAGES_MINIMAL_BY_TYPE[t].get(role, [])
    return _MESSAGES_BY_TYPE[t].get(role, [])


def _build_system_block(harness: dict[str, Any], role: str) -> str:
    from app.roles import ROLE_META
    from app.pipeline_rules import ESCALATION_CHAIN

    meta = ROLE_META.get(role, {})
    persona = meta.get("persona", "You are a helpful AI assistant.")
    escalation_target = ESCALATION_CHAIN.get(role)
    cost_type = harness.get("cost_type", "local")

    chain_lines = []
    if escalation_target:
        target_label = ROLE_META.get(escalation_target, {}).get("title", escalation_target)
        chain_lines.append(f"- You report to: {escalation_target} ({target_label})")
    reports_to_me = [s for s, t in ESCALATION_CHAIN.items() if t == role]
    if reports_to_me:
        chain_lines.append(f"- Workers below you: {', '.join(reports_to_me)}")
    chain_section = "\n\nChain of command:\n" + "\n".join(chain_lines) if chain_lines else ""

    cost_note = ""
    if cost_type == "cloud_metered":
        cost_note = "\n\nToken conservation: metered cloud worker — be concise, do not pad output."
    elif cost_type == "cloud_shared":
        cost_note = "\n\nToken conservation: shared cloud pool — keep responses tight and focused."

    return (
        f"{persona}"
        f"{chain_section}"
        f"\n\nOperational rules:"
        f"\n- Output ESCALATE: <reason> if you cannot complete a task"
        f"\n- Output ESCALATE: context too large — <brief summary> if input exceeds your capacity"
        f"\n- Never explain failures in prose — always use the signal format"
        f"\n- Output only what is requested — no explanations, no preamble, no fences unless explicitly asked"
        f"{cost_note}"
    )


async def generate_modelfile(harness_id: str, ollama_host: str, output_type: str = "yaml") -> dict[str, Any]:
    """Generate a Modelfile for a harness, merging into any existing Modelfile from Ollama."""
    from app.harnesses import get_harness
    from app.roles import load_roles

    harness = get_harness(harness_id)
    if not harness:
        return {"ok": False, "error": f"Harness '{harness_id}' not found"}

    model = harness.get("model", "")
    if not model:
        return {"ok": False, "error": "Harness has no model name"}

    roles = load_roles()
    role = next((r for r, a in roles.items() if a.get("harness_id") == harness_id), None)

    existing = await fetch_modelfile_from_ollama(model, ollama_host)
    existing_fetched = bool(existing)
    if not existing:
        existing = f"FROM {model}\n"
    else:
        # Normalize FROM: blob paths / absolute paths don't work on /api/create
        import re as _re
        existing = _re.sub(r'(?m)^FROM\s+\S+', f'FROM {model}', existing)

    system = _build_system_block(harness, role) if role else (
        "You are an AI worker in the Fleet Command pipeline.\n\n"
        "Operational rules:\n"
        "- Output ESCALATE: <reason> if you cannot complete a task\n"
        "- Output only what is requested — no explanations, no preamble"
    )

    # Build full parameter set: role defaults → harness overrides
    h_params = harness.get("params", {})
    params: dict[str, Any] = {}
    if role:
        params.update(_ROLE_PARAMS.get(role, {}))

    ctx = harness.get("context_window")
    if ctx:
        params["num_ctx"] = ctx
    allowance = harness.get("token_allowance")
    if allowance:
        params["num_predict"] = allowance
    temp = h_params.get("temperature")
    if temp is not None:
        params["temperature"] = temp
        if float(temp) == 0:
            params["seed"] = 42
    for k in ("top_k", "top_p", "min_p"):
        if k in h_params:
            params[k] = h_params[k]

    # Stop sequences
    stops = _ROLE_STOPS.get(role or "", [])

    # MESSAGE few-shot examples — selected by output type + model tier
    ctx_window = harness.get("context_window") or 0
    messages = _get_messages(role or "", ctx_window, output_type)

    # Role-suffixed target model name — e.g. gemma4:reviewer, qwen-ha:generator
    base_model = model.split(":")[0] if ":" in model else model
    target_model = f"{base_model}:{role}" if role else model

    # Build final content, merging into base model's existing Modelfile
    content = _merge_modelfile(existing, system, params, messages)

    # Insert stop sequences manually after other PARAMETERs (stop needs special format)
    if stops:
        stop_block = "\n".join(f'PARAMETER stop "{s}"' for s in stops)
        if "\nMESSAGE " in content:
            idx = content.index("\nMESSAGE ")
            content = content[:idx] + "\n" + stop_block + content[idx:]
        else:
            content += "\n" + stop_block

    already_pushed = is_modelfile_pushed(harness_id)

    return {
        "ok": True,
        "content": content,
        "existing_fetched": existing_fetched,
        "already_pushed": already_pushed,
        "role": role,
        "target_model": target_model,
        "base_model": model,
    }


def _modelfile_to_api_payload(model_name: str, content: str) -> dict:
    """Parse Modelfile content into the structured /api/create payload (Ollama 0.5+)."""
    import re
    system = ""
    params: dict = {}
    messages: list = []
    in_system = False
    in_message = False
    current_msg_role = ""
    current_msg_lines: list[str] = []

    for line in content.splitlines():
        stripped = line.strip()
        upper = stripped.upper()

        if in_message:
            if '"""' in stripped:
                before = stripped[:stripped.index('"""')]
                if before:
                    current_msg_lines.append(before)
                messages.append({"role": current_msg_role, "content": "\n".join(current_msg_lines).strip()})
                in_message = False
                current_msg_lines = []
                current_msg_role = ""
            else:
                current_msg_lines.append(line)
            continue

        if upper.startswith("SYSTEM"):
            rest = stripped[6:].strip()
            if rest.startswith('"""'):
                inner = rest[3:]
                if inner.endswith('"""'):
                    system = inner[:-3].strip()
                elif inner:
                    system = inner
                    in_system = True
                else:
                    in_system = True
            else:
                m = re.match(r'"(.+)"', rest)
                if m:
                    system = m.group(1)
                else:
                    system = rest
            continue

        if in_system:
            if '"""' in stripped:
                before = stripped[:stripped.index('"""')]
                if before:
                    system += ("\n" if system else "") + before
                in_system = False
            else:
                system += ("\n" if system else "") + line
            continue

        if upper.startswith("PARAMETER "):
            parts = stripped.split(None, 2)
            if len(parts) == 3:
                key, val = parts[1], parts[2].strip('"')
                try:
                    params[key] = int(val)
                except ValueError:
                    try:
                        params[key] = float(val)
                    except ValueError:
                        params[key] = val
            continue

        if upper.startswith("MESSAGE "):
            parts = stripped.split(None, 2)
            if len(parts) >= 2:
                current_msg_role = parts[1]
                rest = parts[2].strip() if len(parts) == 3 else ""
                if rest.startswith('"""'):
                    inner = rest[3:]
                    if inner.endswith('"""'):
                        messages.append({"role": current_msg_role, "content": inner[:-3].strip()})
                    elif inner:
                        current_msg_lines = [inner]
                        in_message = True
                    else:
                        current_msg_lines = []
                        in_message = True
                else:
                    messages.append({"role": current_msg_role, "content": rest})
            continue

    payload: dict = {"model": model_name, "from": model_name}
    if system:
        payload["system"] = system.strip()
    if params:
        payload["parameters"] = params
    if messages:
        payload["messages"] = messages
    return payload


async def push_modelfile_to_ollama(harness_id: str, ollama_host: str) -> tuple[bool, str]:
    import httpx
    from app.harnesses import get_harness, save_user_harness
    from app.roles import load_roles

    entry = load_modelfiles().get(harness_id, {})
    content = entry.get("content", "")
    if not content:
        return False, f"No modelfile content saved for harness '{harness_id}'"
    harness = get_harness(harness_id)
    if not harness:
        return False, f"Harness '{harness_id}' not found"
    base_model = harness.get("model", "")
    if not base_model:
        return False, "Harness has no model name"

    # Compute role-suffixed target name
    roles = load_roles()
    role = next((r for r, a in roles.items() if a.get("harness_id") == harness_id), None)
    if role:
        base = base_model.split(":")[0] if ":" in base_model else base_model
        target_model = f"{base}:{role}"
    else:
        target_model = base_model

    try:
        payload = _modelfile_to_api_payload(target_model, content)
        # Push to base model so Ollama can inherit weights, but create as target_model
        payload["from"] = base_model
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{ollama_host}/api/create", json=payload)
            resp.raise_for_status()
        mark_modelfile_pushed(harness_id)

        # Update harness to point to the role-suffixed model
        if target_model != base_model:
            updated = dict(harness)
            updated["model"] = target_model
            updated.pop("_id", None)
            save_user_harness(harness_id, updated)

        return True, f"Created {target_model}"
    except Exception as exc:
        return False, str(exc)
