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
        "List the major UI components (blocks) needed. For each block name what cards it contains.\n"
        "Be concise. Plain text only. No YAML. Under 100 words."
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
        "If not, write REJECTED_AT: <stage> on the first line, then REJECTED: with specific reasons.\n"
        "Valid REJECTED_AT stages: project_manager, manager, generator, reviewer.\n"
        "Choose the stage closest to where the error originated."
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
    Preserves FROM, TEMPLATE, ADAPTER, LICENSE lines.
    Replaces SYSTEM block, our PARAMETER lines, and all MESSAGE lines.
    """
    our_param_keys = {k.lower() for k in new_params}
    lines_out: list[str] = []
    in_system = False
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

    # Append MESSAGE examples
    if new_messages:
        lines_out.append("")
        for role_tag, msg in new_messages:
            lines_out.append(f"MESSAGE {role_tag} {msg}")

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

# Stop sequences per role — prevent common failure patterns
_ROLE_STOPS: dict[str, list[str]] = {
    "generator": ["```", "Here is", "Here's", "Sure,", "Certainly,"],
    "reviewer":  ["```python", "Here is", "Explanation:"],
    "manager":   ["```", "Here is", "Explanation:"],
}


# ── Per-role MESSAGE examples (few-shot) ──────────────────────────────────────

_ROLE_MESSAGES: dict[str, list[tuple[str, str]]] = {
    "generator": [
        ("user", "Task: Add a weather card\nBlock: Weather"),
        ("assistant", "type: weather-forecast\nentity: weather.home"),
        ("user", "Task: Show current temperature\nBlock: Climate"),
        ("assistant", "type: sensor\nentity: sensor.temperature\nname: Temperature"),
        ("user", "Task: Summarize 40 sensors across 15 domains into one card with live updates for all of them"),
        ("assistant", "ESCALATE: context too large — task requires 40+ sensors across 15 domains, exceeds single-card scope"),
    ],
    "reviewer": [
        ("user", "Spec: Weather dashboard\n\nYAML to review:\n```yaml\ntype: weather-forecast\nentity: weather.home\n```"),
        ("assistant", "# REVIEW: Fixed markdown fences.\ntype: weather-forecast\nentity: weather.home"),
        ("user", "Spec: Climate view\n\nYAML to review:\ntype: markdown\nentity: sensor.temp"),
        ("assistant", "# REVIEW: Fixed markdown card using entity instead of content.\ntype: markdown\ncontent: |\n  {{ states('sensor.temp') }}°C"),
        ("user", "Spec: Full home dashboard\n\nYAML to review:\ntitle: Home\nviews:\n  - title: Main\n    cards: [... 800 cards ...]"),
        ("assistant", "ESCALATE: context too large — received YAML with 800+ cards, cannot review in single pass"),
    ],
    "manager": [
        ("user", "Original request: Weather dashboard\n\nPlan:\nBuild a weather block with current conditions and forecast"),
        ("assistant", "BLOCK 1: Weather\n- Task 1: weather-forecast current conditions\n- Task 2: markdown 5-day forecast"),
        ("user", "Original request: Home overview\n\nPlan:\n[plan with 200 blocks and 1500 tasks across 40 domains]"),
        ("assistant", "ESCALATE: context too large — plan contains 200 blocks/1500 tasks, exceeds single breakdown capacity"),
    ],
    "project_manager": [
        ("user", "Job request: Build a simple weather dashboard with current conditions"),
        ("assistant", "Block 1: Weather\n- weather-forecast card\n- temperature sensor card"),
        ("user", "Job request: Full smart home dashboard covering all 47 rooms with live sensors, cameras, automation controls, energy monitoring, and security"),
        ("assistant", "ESCALATE: scope too large for single planning pass — recommend splitting into sub-projects per floor or domain"),
    ],
    "supervisor": [
        ("user", "Job specification:\nWeather dashboard\n\nFinal output:\ntitle: Weather\nviews:\n  - title: Main\n    cards:\n      - type: weather-forecast\n        entity: weather.home"),
        ("assistant", "title: Weather\nviews:\n  - title: Main\n    cards:\n      - type: weather-forecast\n        entity: weather.home"),
        ("user", "Job specification:\nWeather dashboard\n\nFinal output:\ntitle: Weather\nviews:\n  - title: Main\n    cards:\n      - type: weather-forecast\n        sensor: weather.home"),
        ("assistant", "REJECTED_AT: generator\nREJECTED: weather-forecast card uses invalid field 'sensor' — must be 'entity'"),
    ],
    "advisor": [
        ("user", "A generator worker is escalating to you — it cannot complete its task.\n\nReason: context too large — received task requiring 40 sensors\n\nTask context:\nBuild a unified sensor overview card"),
        ("assistant", "Split into multiple cards — one per sensor domain. Generator should output one card per call. Manager should rewrite the task as: 'Task 1: climate sensors card, Task 2: energy sensors card' etc."),
    ],
}


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


async def generate_modelfile(harness_id: str, ollama_host: str) -> dict[str, Any]:
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

    # MESSAGE few-shot examples
    messages = _ROLE_MESSAGES.get(role or "", [])

    # Build final content: params first (including stops), then merge
    stop_lines = {f"stop_{i}": f'stop "{s}"' for i, s in enumerate(stops)}

    content = _merge_modelfile(existing, system, params, messages)

    # Insert stop sequences manually after other PARAMETERs (stop needs special format)
    if stops:
        stop_block = "\n".join(f'PARAMETER stop "{s}"' for s in stops)
        # Insert before MESSAGE block or at end
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
    }


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
    model_name = harness.get("model", "")
    if not model_name:
        return False, "Harness has no model name"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{ollama_host}/api/create",
                json={"name": model_name, "modelfile": content},
            )
            resp.raise_for_status()
        mark_modelfile_pushed(harness_id)
        return True, "OK"
    except Exception as exc:
        return False, str(exc)
