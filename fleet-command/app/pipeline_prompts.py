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
        "Be concise. Plain text only. No YAML. Under 150 words."
    ),
    "manager": (
        "Original request: {spec}\n\n"
        "Plan:\n{prev}\n\n"
        "Output ONLY a block/task list in this exact format. Nothing else:\n\n"
        "BLOCK 1: [name]\n"
        "- Task 1: [card type] [entity] [purpose]\n"
        "- Task 2: [card type] [entity] [purpose]\n\n"
        "BLOCK 2: [name]\n"
        "- Task 1: ...\n\n"
        "Rules: one task = one card. Respect the exact quantities in the original request. No YAML. No explanations. No intro text."
    ),
    "generator": (
        "Block: {block}\n"
        "Task: {task}\n\n"
        "Output a single Home Assistant Lovelace card YAML fragment only.\n"
        "No title. No views wrapper. Card definition only. YAML only."
    ),
    "generator_single": (
        "Build this: {spec}\n\n"
        "Output complete valid Home Assistant Lovelace YAML only."
    ),
    "assembler": (
        "Job specification: {spec}\n\n"
        "Card fragments to assemble:\n{fragments}\n\n"
        "Combine these into one complete valid Home Assistant Lovelace YAML dashboard.\n"
        "Group cards under views by their block name.\n"
        "Output complete YAML only. No explanations. No fences."
    ),
    "reviewer": (
        "Spec: {spec}\n\n"
        "YAML to fix:\n{prev}\n\n"
        "Output the corrected YAML only. No explanations. No comments. YAML only."
    ),
    "supervisor": (
        "Job specification:\n{spec}\n\n"
        "Final output for sign-off:\n{prev}\n\n"
        "If acceptable, return the YAML unchanged. "
        "If not, write REJECTED_AT: <stage> on the first line, then REJECTED: with reasons. "
        "Valid stages: project_manager, manager, generator, reviewer."
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
