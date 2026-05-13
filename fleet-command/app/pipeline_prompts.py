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
