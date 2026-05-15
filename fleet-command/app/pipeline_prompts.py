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

    "reviewer":        ["{spec}", "{prev}"],
    "supervisor":      ["{spec}", "{prev}"],
}

DEFAULT_PROMPTS: dict[str, str] = {
    "project_manager": (
        "Job request: {spec}\n\n"
        "Max tasks per run: {max_tasks}\n\n"
        "Describe the project fully — intent, domain, scope, and what a successful output looks like.\n"
        "If total task count fits within {max_tasks}: list the major blocks and what each contains. Plain text only. No code. Under 150 words.\n"
        "If total task count exceeds {max_tasks}: split into self-contained sub-jobs, each under {max_tasks} tasks. Output only:\n"
        "SUB-JOB 1: [self-contained scope description]\n"
        "SUB-JOB 2: [self-contained scope description]\n"
        "One line per sub-job. No extra text. No code."
    ),
    "manager": (
        "Original request: {spec}\n\n"
        "Plan:\n{prev}\n\n"
        "Break the plan into blocks and tasks. For each task write a complete brief the worker can act on directly — "
        "include what to build, the structure to follow, relevant reference examples or patterns, and any constraints.\n\n"
        "Format:\n"
        "BLOCK 1: [name]\n"
        "- Task 1: [type] — [purpose] — Reference: [pattern or example the worker should follow]\n"
        "- Task 2: [type] — [purpose]\n\n"
        "BLOCK 2: [name]\n"
        "- Task 1: ...\n\n"
        "One task = one unit of work. No code output. No explanations outside task briefs."
    ),
    "generator": (
        "Task: {task}\n"
        "Block: {block}\n\n"
        "Output the requested code only. Follow the reference and structure provided in the task brief exactly.\n"
        "No explanation. No fences. No wrapper."
    ),
    "generator_single": (
        "Build this: {spec}\n\n"
        "Output complete valid code. Include all required structure.\n"
        "No explanation. No fences."
    ),
    "reviewer": (
        "Spec: {spec}\n\n"
        "Output to review:\n{prev}\n\n"
        "Review the output against the spec and any references. Fix small issues directly.\n"
        "If you need clarification on structure or intent, send [COMM] to:manager before proceeding.\n"
        "Return your result as two separate messages:\n"
        "Message 1: [COMM] to:manager — review: [passed/fixed/failed] — [what was checked, what was fixed, or why it failed]\n"
        "Message 2: corrected output only — no explanation, no fences"
    ),
    "supervisor": (
        "Job specification:\n{spec}\n\n"
        "Final output for sign-off:\n{prev}\n\n"
        "If the output fulfils the spec: approve and deliver.\n"
        "If there are fixable issues: send [COMM] to:project_manager with reprocess instructions — route to the correct stage, specify exactly what to fix.\n"
        "If the output has a fundamental failure (wrong scope, unrecoverable errors, spec completely missed): output:\n"
        "REJECTED: <specific reason>\n"
        "CORRECTIVE_BRIEF: <exact instructions for what must change before retry>\n"
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


def mark_modelfile_pushed(harness_id: str, target_model: str = "") -> None:
    from datetime import datetime, timezone
    data = load_modelfiles()
    entry = data.get(harness_id, {})
    entry["pushed"] = True
    entry["pushed_at"] = datetime.now(timezone.utc).isoformat()
    if target_model:
        entry["target_model"] = target_model
    data[harness_id] = entry
    _save_modelfiles(data)


def get_pushed_model(harness_id: str) -> str | None:
    """Return the pushed model name (role-suffixed) if the modelfile is active, else None."""
    entry = load_modelfiles().get(harness_id, {})
    if not entry.get("pushed"):
        return None
    return entry.get("target_model") or None


def reset_modelfile_pushed(harness_id: str) -> None:
    """Mark modelfile as not pushed — called when Ollama can't find the pushed model."""
    data = load_modelfiles()
    entry = data.get(harness_id, {})
    entry["pushed"] = False
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

# Stop sequences — emitted as a single JSON array (Ollama 0.22.1+ rejects repeated PARAMETER stop lines)
_ROLE_STOPS: dict[str, list[str]] = {
    "generator": ["Here is", "Here's", "Sure,", "Certainly,"],
    "reviewer":  ["```python", "Here is", "Explanation:"],
    "manager":   ["Here is", "Explanation:"],
}


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
            "Task: [task type and purpose]\nBlock: [block name]",
        ),
        (
            "assistant",
            "*output here — definition only, no wrapper, no fences*",
        ),
        (
            "user",
            "Task: [task type]\nBlock: [block name]\n\nAdditional instructions: [corrective brief — structure to use, what to avoid]",
        ),
        (
            "assistant",
            "*output here — all corrective instructions applied*",
        ),
    ],
    "manager": [
        (
            "user",
            "Original request: [job description]\n\nPlan:\n[PM block summary]",
        ),
        (
            "assistant",
            "BLOCK 1: [name]\n- Task 1: [task type and purpose]\n- Task 2: [task type and purpose]",
        ),
        (
            "user",
            "Original request: [job description]\n\nPlan:\n[PM block summary]\n\nAdditional instructions: [corrective brief — how to restructure]",
        ),
        (
            "assistant",
            "BLOCK 1: [restructured name per brief]\n- Task 1: [adjusted task type]",
        ),
    ],
    "project_manager": [
        (
            "user",
            "Job request: [job description]\n\nMax tasks per run: [limit]",
        ),
        (
            "assistant",
            "BLOCK 1: [domain]\n- [task type]\n\nBLOCK 2: [domain]\n- [task type]",
        ),
        (
            "user",
            "Job request: [job description]\n\nMax tasks per run: [limit]\n\nAdditional instructions: [corrective brief — scope constraints]",
        ),
        (
            "assistant",
            "BLOCK 1: [rescoped domain per brief]\n- [task type per constraints]",
        ),
    ],
    "reviewer": [
        (
            "user",
            "Spec: [job description]\n\nOutput to review:\n*assembled output here*",
        ),
        (
            "assistant",
            "[COMM] to:manager — review: [passed/fixed/failed] — [notes]",
        ),
        (
            "user",
            "[COMM] pipeline → send output",
        ),
        (
            "assistant",
            "*reviewed output here*",
        ),
    ],
    "supervisor": [
        (
            "user",
            "[COMM] project_manager → sign-off: approved — project: [ID], spec: [summary], chain: [stages ran]",
        ),
        (
            "assistant",
            "[COMM] pipeline → approved — delivering output",
        ),
        (
            "user",
            "[COMM] pipeline → send output",
        ),
        (
            "assistant",
            "[final approved output]",
        ),
    ],
}

# ── Per-role MESSAGE examples (few-shot, yaml output type) ────────────────────

_ROLE_MESSAGES_YAML: dict[str, list[tuple[str, str]]] = {
    "generator": [
        # 1. Normal build → output card YAML only
        (
            "user",
            "Task: [card type and purpose]\n"
            "Block: [block name]",
        ),
        (
            "assistant",
            "*yaml card output here — card definition only, no wrapper, no fences*",
        ),
        # 2. Corrective brief injected — follow all instructions exactly
        (
            "user",
            "Task: [card type]\n"
            "Block: [block name]\n\n"
            "Additional instructions: [corrective brief — specific card types, structure to use, what to avoid]",
        ),
        (
            "assistant",
            "*yaml card output here — all corrective instructions applied*",
        ),
        # 3. Retry after rejection — address all rejection remarks
        (
            "user",
            "Task: [card type]\n"
            "Block: [block name]\n\n"
            "This is a retry. Previous attempt was rejected:\n"
            "[rejection reason]\n\n"
            "Address all rejection remarks in your output.",
        ),
        (
            "assistant",
            "*corrected yaml card here — all rejection remarks addressed*",
        ),
        # 4. Reference content pre-embedded by manager — use it, output card
        (
            "user",
            "Task: [card type and purpose]\n"
            "Block: [block name]\n\n"
            "Reference:\n"
            "[relevant section content provided by manager]",
        ),
        (
            "assistant",
            "*yaml card output here — built using provided reference*",
        ),
        # 5a. [COMM] irregular — manager sends correction mid-task
        (
            "user",
            "[COMM] manager → correction: [what was wrong and what to use instead]\n\n"
            "Task: [card type]\n"
            "Block: [block name]",
        ),
        (
            "assistant",
            "*yaml card output here — correction applied*",
        ),
        # 5b. [COMM] irregular — generator asks structural question, manager answers, generator outputs
        (
            "user",
            "Task: [card type with ambiguous structure]\n"
            "Block: [block name]",
        ),
        (
            "assistant",
            "[COMM] to:manager — question: [structural or scope question — not about entity IDs]",
        ),
        (
            "user",
            "[COMM] manager → [direct answer to structural question]",
        ),
        (
            "assistant",
            "*yaml card output here — built using manager's answer*",
        ),
        # 5c. [COMM] irregular — cross-worker, fragment received from another worker to fix or incorporate
        (
            "user",
            "[COMM] [worker role] → [context: what is being sent and why — fix request, answer, or fragment]\n\n"
            "[content from the other worker]",
        ),
        (
            "assistant",
            "*yaml card output here — incorporating content from [worker role]*",
        ),
    ],
    "manager": [
        # 1. PM plan → structured BLOCK/task breakdown
        (
            "user",
            "Original request: [job description]\n\n"
            "Plan:\n"
            "[PM block summary]",
        ),
        (
            "assistant",
            "BLOCK 1: [name]\n"
            "- Task 1: [task type and purpose]\n"
            "- Task 2: [task type and purpose]\n\n"
            "BLOCK 2: [name]\n"
            "- Task 1: [task type and purpose]",
        ),
        # 2. Corrective brief — restructure failing blocks/tasks
        (
            "user",
            "Original request: [job description]\n\n"
            "Plan:\n"
            "[PM block summary]\n\n"
            "Additional instructions: [corrective brief — how to restructure blocks or tasks to fix the failure]",
        ),
        (
            "assistant",
            "BLOCK 1: [restructured name per corrective brief]\n"
            "- Task 1: [adjusted task type and purpose]\n"
            "- Task 2: [adjusted task type and purpose]",
        ),
        # 3. Generator [COMM] unclear instructions → manager clarifies → generator proceeds
        (
            "user",
            "[COMM] generator → unclear instructions: [what is ambiguous about the task brief]",
        ),
        (
            "assistant",
            "[COMM] to:generator — clarification: [direct answer resolving the ambiguity]",
        ),
        (
            "user",
            "[COMM] pipeline → generator acknowledged, proceeding",
        ),
        (
            "assistant",
            "[COMM] to:project_manager — generator unblocked, task [N] continuing",
        ),
        # 4. Plan too large → ESCALATE to PM
        (
            "user",
            "Original request: [very large multi-domain job]\n\n"
            "Plan:\n"
            "[PM plan with too many blocks / tasks to break down in one pass]",
        ),
        (
            "assistant",
            "ESCALATE: plan too large — [N] blocks and [M]+ tasks exceeds single breakdown pass, recommend PM splits into separate sub-jobs per domain",
        ),
        # 5. Sign-off: all tasks present → approve and pass to PM
        (
            "user",
            "Generator has completed all tasks. Assembled fragments follow.\n\n"
            "Expected tasks:\n"
            "  - [Block A] task 1\n"
            "  - [Block A] task 2\n"
            "  - [Block B] task 1\n\n"
            "# Block: Block A\n"
            "[task 1 output]\n"
            "[task 2 output]\n\n"
            "# Block: Block B\n"
            "[task 1 output]",
        ),
        (
            "assistant",
            "[COMM] to:project_manager — sign-off: approved — all [N] tasks present, [M] blocks complete",
        ),
        (
            "user",
            "[COMM] pipeline → send assembled output",
        ),
        (
            "assistant",
            "[assembled output structured by block]",
        ),
        # 6. Sign-off: missing task → gap-fill → incorporate → approve
        (
            "user",
            "Generator has completed all tasks. Assembled fragments follow.\n\n"
            "Expected tasks:\n"
            "  - [Block A] task 1\n"
            "  - [Block A] task 2\n\n"
            "# Block: Block A\n"
            "[task 2 output only — task 1 missing]",
        ),
        (
            "assistant",
            "[COMM] to:generator — task 1/2 missing, block: Block A, purpose: [what is needed]",
        ),
        (
            "user",
            "[COMM] pipeline → generator returned missing task 1/2, block: Block A\n\n"
            "[task 1 output]",
        ),
        (
            "assistant",
            "[COMM] to:project_manager — sign-off: approved — gap filled, all [N] tasks present",
        ),
        (
            "user",
            "[COMM] pipeline → send assembled output",
        ),
        (
            "assistant",
            "[assembled output with gap-fill incorporated]",
        ),
        # 7. Chunked sign-off — input too large, process this chunk only
        (
            "user",
            "[COMM] pipeline → chunk 1/2 — structure only these blocks, output only this chunk\n\n"
            "# Block: [Block A]\n"
            "[task outputs]",
        ),
        (
            "assistant",
            "[assembled output for chunk 1 only]",
        ),
        # 8. [COMM] irregular — manager asks PM for clarification
        (
            "user",
            "[original request or plan with ambiguous scope or conflicting instructions]",
        ),
        (
            "assistant",
            "[COMM] to:project_manager — question: [specific scope or structure question that cannot be resolved from the brief alone]",
        ),
        (
            "user",
            "[COMM] project_manager → [direct answer]",
        ),
        (
            "assistant",
            "BLOCK 1: [name resolved per PM answer]\n"
            "- Task 1: [task type and purpose]",
        ),
    ],
    "project_manager": [
        # 1. Normal spec within task limit → block list to manager
        (
            "user",
            "Job request: [job description that fits within max_tasks]\n\n"
            "Max tasks per run: [limit]",
        ),
        (
            "assistant",
            "BLOCK 1: [domain]\n"
            "- [task type]\n"
            "- [task type]\n\n"
            "BLOCK 2: [domain]\n"
            "- [task type]",
        ),
        # 2. Corrective brief from supervisor → re-scope and replan
        (
            "user",
            "Job request: [job description]\n\n"
            "Max tasks per run: [limit]\n\n"
            "Additional instructions: [corrective brief — scope constraints, blocks to simplify or remove]",
        ),
        (
            "assistant",
            "BLOCK 1: [rescoped domain per corrective brief]\n"
            "- [task type per constraints]\n\n"
            "BLOCK 2: [rescoped domain]\n"
            "- [task type per constraints]",
        ),
        # 3. Scope exceeds limit → split into sub-jobs
        (
            "user",
            "Job request: [multi-domain job that exceeds max_tasks]\n\n"
            "Max tasks per run: [limit]",
        ),
        (
            "assistant",
            "SUB-JOB 1: [self-contained domain scope]\n"
            "SUB-JOB 2: [self-contained domain scope]\n"
            "SUB-JOB 3: [self-contained domain scope]",
        ),
        # 4. Single domain too large to split → ESCALATE to supervisor
        (
            "user",
            "Job request: [single domain so large it cannot be split under the task limit]\n\n"
            "Max tasks per run: [limit]",
        ),
        (
            "assistant",
            "ESCALATE: single domain exceeds run limit — [reason] cannot be split into sub-jobs each under [limit] tasks without losing coherence, requires manual scoping",
        ),
        # 5. Manager sign-off received (approved) → PM approves, [COMM] to supervisor with project notes
        (
            "user",
            "[COMM] manager → sign-off: approved — all [N] tasks present, [M] blocks complete",
        ),
        (
            "assistant",
            "[COMM] to:supervisor — sign-off: approved — project: [ID], spec: [one-line summary], chain: [stages that ran], flags: [any escalations or corrections that occurred — who flagged, what stage, what was done], substitutions: [anything replaced or skipped and why], open-items: [anything requiring user attention]",
        ),
        (
            "user",
            "[COMM] pipeline → send output",
        ),
        (
            "assistant",
            "[final assembled output]",
        ),
        # 6. Manager sign-off received (rejected) → PM rejects, [COMM] to supervisor with failure details
        (
            "user",
            "[COMM] manager → sign-off: rejected — [stage], reason: [why it failed]",
        ),
        (
            "assistant",
            "[COMM] to:supervisor — sign-off: rejected — project: [ID], spec: [one-line summary], chain: [stages that ran], failed-at: [stage], flagged-by: [worker], reason: [specific failure], attempted: [corrective action tried], recommendation: [what needs to change before retry]",
        ),
        # 7. [COMM] irregular — manager asks PM for clarification
        (
            "user",
            "[COMM] manager → question: [scope or structure question that cannot be resolved from the brief]",
        ),
        (
            "assistant",
            "[COMM] to:manager — clarification: [direct answer resolving the ambiguity]",
        ),
        # 8. [COMM] irregular — PM asks supervisor for clarification
        (
            "user",
            "[job request or brief with conflicting requirements or unclear scope]",
        ),
        (
            "assistant",
            "[COMM] to:supervisor — question: [specific question about scope, priority, or constraints that cannot be resolved from the spec alone]",
        ),
        (
            "user",
            "[COMM] supervisor → [direct answer]",
        ),
        (
            "assistant",
            "BLOCK 1: [domain resolved per supervisor answer]\n"
            "- [task type]",
        ),
    ],
    "reviewer": [
        # Valid output — pass through unchanged
        (
            "user",
            "Spec: [job description]\n\n"
            "Output to review:\n"
            "*assembled output here — N blocks, M cards*",
        ),
        (
            "assistant",
            "[COMM] to:manager — review: passed — no issues found",
        ),
        (
            "user",
            "[COMM] pipeline → send output",
        ),
        (
            "assistant",
            "*full output returned unchanged*",
        ),
        # Fixable issues found — corrected
        (
            "user",
            "Spec: [job description]\n\n"
            "Output to review:\n"
            "*assembled output here — contains invalid fields, leaked fences, or wrong structure*",
        ),
        (
            "assistant",
            "[COMM] to:manager — review: fixed — [brief description of what was corrected]",
        ),
        (
            "user",
            "[COMM] pipeline → send output",
        ),
        (
            "assistant",
            "*full corrected output here*",
        ),
        # Corrective brief injected — apply and fix
        (
            "user",
            "Spec: [job description]\n\n"
            "Output to review:\n"
            "*assembled output here*\n\n"
            "Additional instructions: [corrective brief — specific issues to look for and fix]",
        ),
        (
            "assistant",
            "[COMM] to:manager — review: fixed — applied corrective brief, [what was done]",
        ),
        (
            "user",
            "[COMM] pipeline → send output",
        ),
        (
            "assistant",
            "*full corrected output per brief*",
        ),
        # Failed — issues found that cannot be resolved by reviewer
        (
            "user",
            "Spec: [job description]\n\n"
            "Output to review:\n"
            "*assembled output here — contains unresolvable structural errors*",
        ),
        (
            "assistant",
            "[COMM] to:manager — review: failed — [specific reason, what could not be fixed]",
        ),
        (
            "user",
            "[COMM] pipeline → send output",
        ),
        (
            "assistant",
            "*output with inline annotations marking failures*",
        ),
        # Context too large → escalate
        (
            "user",
            "Spec: [job description]\n\n"
            "Output to review:\n"
            "*assembled output — very large, exceeds context capacity*",
        ),
        (
            "assistant",
            "ESCALATE: context too large — [N]-item output cannot be reviewed in a single pass, chunking required",
        ),
    ],
    "supervisor": [
        # 1. Output valid → approve and deliver, no chain notification
        (
            "user",
            "[COMM] project_manager → sign-off: approved — project: [ID], spec: [summary], chain: [stages ran], flags: [any notable events]",
        ),
        (
            "assistant",
            "[COMM] pipeline → approved — delivering output",
        ),
        (
            "user",
            "[COMM] pipeline → send output",
        ),
        (
            "assistant",
            "[final approved output]",
        ),
        # 2. Fixable issues → route back to PM for reprocessing, do not reject
        (
            "user",
            "[COMM] project_manager → sign-off: approved — project: [ID], spec: [summary], chain: [stages ran], flags: [escalations or corrections]\n\n"
            "[output with issues that do not warrant full rejection]",
        ),
        (
            "assistant",
            "[COMM] to:project_manager — reprocess: [what needs fixing], route-to: [stage], instructions: [specific corrections to apply]",
        ),
        # 3. Acute failure → hard REJECTED, chain only involved if project fails
        (
            "user",
            "[COMM] project_manager → sign-off: rejected — project: [ID], spec: [summary], chain: [stages ran], failed-at: [stage], reason: [failure], attempted: [what was tried]",
        ),
        (
            "assistant",
            "REJECTED: [specific reason — fundamental spec violation, wrong scope, or unrecoverable failure]\n"
            "REJECTED_AT: [reviewer|manager|project_manager — where the fix must be applied]\n"
            "CORRECTIVE_BRIEF: [exact instructions for that stage — what must change, what to avoid, minimum acceptable output]",
        ),
        # 4. PM asks supervisor for clarification → supervisor answers directly
        (
            "user",
            "[COMM] project_manager → question: [scope, priority, or constraint question]",
        ),
        (
            "assistant",
            "[COMM] to:project_manager — [direct authoritative answer]",
        ),
        # 5. Supervisor hard judgment call → routes to PM, not advisor (advisor dormant)
        (
            "user",
            "[COMM] project_manager → sign-off: [status] — project: [ID]\n\n"
            "[output or situation requiring a judgment call beyond supervisor's scope]",
        ),
        (
            "assistant",
            "[COMM] to:project_manager — judgment-required: [what needs a decision], context: [relevant details], options: [possible approaches — supervisor does not decide unilaterally]",
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


def _role_specific_rules(role: str) -> str:
    if role == "supervisor":
        return (
            f"\n\nRejection routing — ALWAYS include both lines when rejecting:"
            f"\n  REJECTED: <specific reason>"
            f"\n  REJECTED_AT: <stage>  ← mandatory — tells the pipeline where to route the fix"
            f"\n  CORRECTIVE_BRIEF: <exact actionable instructions for that stage>"
            f"\n"
            f"\nStage routing table:"
            f"\n  REJECTED_AT: reviewer    — entity IDs are wrong, YAML structure malformed, bad nesting, missing view/cards wrapper"
            f"\n  REJECTED_AT: manager     — card content wrong, missing card property (e.g. forecast_type), wrong card type, task was misspecified"
            f"\n  REJECTED_AT: project_manager — wrong scope, wrong number of views, spec violated at planning level"
            f"\n"
            f"\nIf unsure: use REJECTED_AT: manager — manager re-tasks the generator for the specific fragment."
            f"\nNever reject without REJECTED_AT. Never send REJECTED_AT without CORRECTIVE_BRIEF."
        )
    if role == "reviewer":
        return (
            f"\n\nRework protocol — for content errors in a specific fragment (wrong card type, missing required property):"
            f"\n  [COMM] to:manager — task <N>/<total>, block: <block name>: <specific problem and analysis>. Redefine this task and send corrected YAML."
            f"\n  The pipeline will have manager re-brief and generator re-execute that exact task."
            f"\n  You will receive: [COMM] pipeline → rework complete\\n<corrected fragment>"
            f"\n  Integrate the corrected fragment into your output and continue review."
            f"\n"
            f"\nOnly use rework for content errors — not for entity IDs (Pass 1 handles those) and not for structure (Pass 2/3 handle those)."
            f"\nOne rework COMM per review session — do not chain multiple rework requests."
        )
    return ""


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
        f"\n\nCommunication protocol:"
        f"\n- In irregular situations (task unclear, routing to a specific worker, or requesting a missing piece),"
        f"\n  start your response with: [COMM] to:<role> — <reason and context>"
        f"\n  The pipeline will route to that worker and return their response. Then send your output."
        f"\n- When routing or requesting code from another worker, always include location context in your [COMM]:"
        f"\n  [COMM] to:<role> — task <X>/<total>, block: <block name>, purpose: <what is needed>"
        f"\n- Questions must be about code/structure/scope decisions — never about entity IDs (reviewer handles entities)."
        f"\n  Example: [COMM] to:manager — question: spec says animated gauge but standard gauge has no animation. Use history-graph or custom card?"
        f"\n  Example: [COMM] to:project_manager — question: spec requests 14 cards but pipeline limit is 12. Split sub-jobs or drop lower-priority cards?"
        f"\n- When you receive [COMM] pipeline → chunk <N>/<M>: you are receiving part <N> of <M>."
        f"\n  Process only this portion. Output only your result for this chunk. Do not reference other chunks."
        f"\n- When you receive [COMM] pipeline → index: <filename>: a reference index has been loaded for you."
        f"\n  Format per line: <section-id> | <line-range> | <summary>"
        f"\n  To fetch a section: [COMM] to:pipeline — ref:<filename>, section:<section-id>"
        f"\n  The pipeline will return [COMM] pipeline → ref:<section-id> with the exact content."
        f"\n- When you receive any [COMM] prefix: read it as context only — never echo it in your output."
        f"\n- In normal situations, send your output directly — no [COMM] needed."
        f"{_role_specific_rules(role)}"
        f"{cost_note}"
    )


async def generate_modelfile(harness_id: str, ollama_host: str, output_type: str = "yaml", target_name: str = "") -> dict[str, Any]:
    """Generate a Modelfile for a harness, merging into any existing Modelfile from Ollama.

    target_name: optional user-defined model name (e.g. 'qwen-fleet-code'). Once set and pushed,
    subsequent pushes reuse the same name so the model accumulates improvements without losing its name.
    If not provided, uses the previously stored name or auto-derives from base:role.
    """
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

    # Determine target model name — priority: explicit arg > stored frozen name > auto-derive
    stored_entry = load_modelfiles().get(harness_id, {})
    stored_target = stored_entry.get("target_model", "")
    if target_name:
        target_model = target_name.strip()
    elif stored_target:
        target_model = stored_target  # frozen from last push — don't change it
    else:
        base_name = model.split(":")[0] if ":" in model else model
        target_model = f"{base_name}:{role}" if role else model

    # The base model to pull weights from — always the harness model field, never the pushed name
    base_model = stored_entry.get("base_model") or model

    existing = await fetch_modelfile_from_ollama(base_model, ollama_host)
    existing_fetched = bool(existing)
    if not existing:
        existing = f"FROM {base_model}\n"
    else:
        import re as _re
        existing = _re.sub(r'(?m)^FROM\s+\S+', f'FROM {base_model}', existing)

    system = _build_system_block(harness, role) if role else (
        "You are an AI worker in the Fleet Command pipeline.\n\n"
        "Operational rules:\n"
        "- Output ESCALATE: <reason> if you cannot complete a task\n"
        "- Output only what is requested — no explanations, no preamble"
    )

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

    stops = _ROLE_STOPS.get(role or "", [])

    ctx_window = harness.get("context_window") or 0
    messages = _get_messages(role or "", ctx_window, output_type)

    content = _merge_modelfile(existing, system, params, messages)

    if stops:
        stop_block = f"PARAMETER stop {json.dumps(stops)}"
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
        "base_model": base_model,
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
                key, raw = parts[1], parts[2].strip()
                # JSON array value (e.g. stop ["a","b"]) — parse directly
                if raw.startswith("["):
                    try:
                        parsed = json.loads(raw)
                        if key in params and isinstance(params[key], list):
                            params[key].extend(parsed)
                        else:
                            params[key] = parsed
                    except Exception:
                        params[key] = raw
                else:
                    val = raw.strip('"')
                    try:
                        parsed_val = int(val)
                    except ValueError:
                        try:
                            parsed_val = float(val)
                        except ValueError:
                            parsed_val = val
                    # accumulate repeated stop lines into a list
                    if key == "stop":
                        if key in params:
                            existing = params[key]
                            params[key] = (existing if isinstance(existing, list) else [existing]) + [parsed_val]
                        else:
                            params[key] = [parsed_val]
                    else:
                        params[key] = parsed_val
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
    from app.harnesses import get_harness

    entry = load_modelfiles().get(harness_id, {})
    content = entry.get("content", "")
    if not content:
        return False, f"No modelfile content saved for harness '{harness_id}'"
    harness = get_harness(harness_id)
    if not harness:
        return False, f"Harness '{harness_id}' not found"

    # Use stored names from generate step — never re-derive on push
    target_model = entry.get("target_model", "")
    base_model = entry.get("base_model") or harness.get("model", "")
    if not target_model or not base_model:
        return False, "Generate the Modelfile first — target_model or base_model not stored"

    try:
        payload = _modelfile_to_api_payload(target_model, content)
        payload["from"] = base_model
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{ollama_host}/api/create", json=payload)
            resp.raise_for_status()
        mark_modelfile_pushed(harness_id, target_model)
        return True, f"Created {target_model}"
    except Exception as exc:
        return False, str(exc)
