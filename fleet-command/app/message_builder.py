from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_BLUEPRINTS_DIR = Path(__file__).parent.parent / "blueprints"

# How much of the context window a message is allowed to consume (input side)
_INPUT_HEADROOM = 0.80


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _ctx_window(harness: dict[str, Any]) -> int | None:
    return harness.get("context_window") or None


def fits_in_window(text_system: str, text_user: str, harness: dict[str, Any]) -> bool:
    ctx = _ctx_window(harness)
    if not ctx:
        return False
    return _estimate_tokens(text_system + text_user) <= int(ctx * _INPUT_HEADROOM)


def overflow_info(text_system: str, text_user: str, harness: dict[str, Any]) -> dict[str, Any]:
    """Returns dict with estimated tokens, ctx_window, pct, and overflows bool."""
    ctx = _ctx_window(harness)
    est = _estimate_tokens(text_system + text_user)
    if not ctx:
        return {
            "estimated": est,
            "ctx_window": None,
            "pct": None,
            "overflows": True,
            "reason": "unknown_context_window",
        }
    pct = int(est / ctx * 100)
    return {
        "estimated": est,
        "ctx_window": ctx,
        "pct": pct,
        "overflows": est > int(ctx * _INPUT_HEADROOM),
    }


def remaining_input_budget(text_system: str, text_user: str, harness: dict[str, Any]) -> int:
    """Approximate tokens still available inside the input headroom."""
    ctx = _ctx_window(harness)
    if not ctx:
        return 0
    used = _estimate_tokens(text_system + text_user)
    return max(0, int(ctx * _INPUT_HEADROOM) - used)


def _trim_to_token_budget(text: str, budget: int) -> str:
    """Trim text to approximately budget tokens (char-based). Appends truncation notice."""
    char_limit = budget * 4
    if len(text) <= char_limit:
        return text
    return text[:char_limit] + "\n[...truncated to fit context window]"


MIN_USEFUL_OUTPUT = 256


def harness_can_respond(harness: dict[str, Any], min_output_tokens: int = MIN_USEFUL_OUTPUT) -> bool:
    """Return False if token_allowance is explicitly set and too small for a useful response."""
    allowance = harness.get("token_allowance")
    if allowance is None:
        return True
    try:
        return int(allowance) >= min_output_tokens
    except (TypeError, ValueError):
        return True


def available_input_tokens(
    harness: dict[str, Any],
    fixed_content: str = "",
    min_output_tokens: int = MIN_USEFUL_OUTPUT,
) -> int | None:
    """Tokens available for variable content after fixed_content and output headroom.
    Returns None if context_window is unknown."""
    ctx = _ctx_window(harness)
    if not ctx:
        return None
    fixed = _estimate_tokens(fixed_content)
    return max(0, int(ctx * _INPUT_HEADROOM) - fixed - min_output_tokens)


def chunk_to_fit(
    items: list[str],
    harness: dict[str, Any],
    fixed_prefix: str = "",
    min_output_tokens: int = MIN_USEFUL_OUTPUT,
) -> list[list[str]]:
    """Split items into batches that each fit within the harness context window.

    Sizing: fixed_prefix + batch content must fit within context_window * INPUT_HEADROOM,
    leaving min_output_tokens headroom for the response.

    - Unknown context_window → returns [items] as one batch; overflow_info in _call_harness catches it.
    - Single item too large for budget → returned alone in its own batch; caller should escalate.
    """
    budget = available_input_tokens(harness, fixed_prefix, min_output_tokens)
    if budget is None:
        return [items]

    batches: list[list[str]] = []
    batch: list[str] = []
    batch_tokens = 0

    for item in items:
        item_tokens = _estimate_tokens(item)
        if batch and batch_tokens + item_tokens > budget:
            batches.append(batch)
            batch = [item]
            batch_tokens = item_tokens
        else:
            batch.append(item)
            batch_tokens += item_tokens

    if batch:
        batches.append(batch)

    return batches if batches else [items]


# TODO: blueprint system — reserved for full dashboard templates (multi-view, multi-block specs).
# Not used by active pipeline. Wire in after per-card/per-block generation is stable.
def load_blueprint(blueprint_id: str) -> dict[str, Any] | None:
    """Load a blueprint JSON file by id. Returns None if not found."""
    for path in _BLUEPRINTS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("id") == blueprint_id:
                return data
        except Exception:
            continue
    return None


def list_blueprints() -> list[dict[str, Any]]:
    """Return summary list of all available blueprints."""
    results = []
    for path in _BLUEPRINTS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append({
                "id": data.get("id"),
                "name": data.get("name"),
                "output_type": data.get("output_type"),
                "archetype": data.get("archetype"),
                "keywords": data.get("keywords", []),
                "task_count": len(data.get("tasks", [])),
            })
        except Exception:
            continue
    return results


def build_stage_message(
    role: str,
    spec: str,
    harness: dict[str, Any],
    blueprint: dict[str, Any] | None = None,
) -> str:
    """
    Construct a role-appropriate message from spec + optional blueprint,
    sized to fit within the harness's context window.

    Role routing:
      supervisor / ceo  → ceo_criteria + spec
      project_manager   → leadership_brief + container summary + task list
      reviewer          → review_criteria checklist
      generator         → spec only (generator receives one task at a time from PM,
                          never the full blueprint — see architecture notes)
      all others        → spec only
    """
    ctx = _ctx_window(harness)
    # Token budget for the message content (80% of window, rest for output)
    budget = int(ctx * _INPUT_HEADROOM) if ctx else None

    if blueprint is None or role == "generator":
        return spec

    if role in ("supervisor", "ceo"):
        ceo = blueprint.get("ceo_criteria", "")
        msg = f"Job specification:\n{spec}"
        if ceo:
            msg += f"\n\nApproval criteria:\n{ceo}"
        if budget:
            msg = _trim_to_token_budget(msg, budget)
        return msg

    if role == "reviewer":
        criteria = blueprint.get("review_criteria", [])
        if not criteria:
            return spec
        checklist = "\n".join(f"{i+1}. {c}" for i, c in enumerate(criteria))
        msg = (
            f"Job specification:\n{spec}\n\n"
            f"Review checklist — verify each item in the output:\n{checklist}\n\n"
            "For each item: mark PASS or FAIL. Then output the corrected YAML."
        )
        if budget:
            msg = _trim_to_token_budget(msg, budget)
        return msg

    if role == "project_manager":
        brief = blueprint.get("leadership_brief", "")
        container = blueprint.get("container", {})
        tasks = blueprint.get("tasks", [])

        # Build compact task list for PM dispatch
        task_lines = []
        for t in tasks:
            requires = [s["id"] for s in t.get("slots", [])]
            req_str = f" [requires: {', '.join(requires)}]" if requires else ""
            task_lines.append(f"- Task '{t['id']}' ({t['card_type']}){req_str}: {t['role']}")

        container_type = container.get("type", "")
        container_nav = container.get("nav_mechanism", "")

        msg = (
            f"Job specification:\n{spec}\n\n"
            f"Blueprint: {blueprint.get('name','')}\n"
            f"Container: {container_type} (nav: {container_nav})\n\n"
        )
        if brief:
            msg += f"Context:\n{brief}\n\n"
        if task_lines:
            msg += f"Tasks to dispatch (one generator call per task):\n" + "\n".join(task_lines)

        if budget:
            msg = _trim_to_token_budget(msg, budget)
        return msg

    # Default — spec only
    return spec
