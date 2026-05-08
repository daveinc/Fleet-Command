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
        "Card format reference:\n"
        "  Markdown card:\n"
        "    type: markdown\n"
        "    content: |\n"
        "      ## Heading\n"
        "      Body text or {{ states('sensor.example') }}\n"
        "  Entities card:\n"
        "    type: entities\n"
        "    title: Title\n"
        "    entities:\n"
        "      - entity: sensor.example\n"
        "  Gauge card:\n"
        "    type: gauge\n"
        "    entity: sensor.example\n"
        "    name: Label\n\n"
        "HA template syntax (use inside content: only):\n"
        "  Sensor value:  {{ states('sensor.name') }}\n"
        "  Date/time:     {{ now().strftime('%Y-%m-%d %H:%M') }}\n"
        "  Attribute:     {{ state_attr('sensor.name', 'attr') }}\n\n"
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
        "Check: valid Lovelace structure, correct card types, no sensor definitions inside views, markdown cards use 'content:' not 'entity:'.\n"
        "If valid: return the YAML unchanged.\n"
        "If invalid: return corrected YAML only. No explanations. No comments. YAML only."
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
