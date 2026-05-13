from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

_DIR = Path("/data/domain_templates")


def _dir() -> Path:
    _DIR.mkdir(parents=True, exist_ok=True)
    return _DIR


def list_domain_templates() -> list[dict[str, Any]]:
    result = []
    for f in sorted(_dir().glob("*.json")):
        try:
            result.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return result


def get_domain_template(tid: str) -> dict[str, Any] | None:
    p = _dir() / f"{tid}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_domain_template(tid: str, data: dict[str, Any]) -> None:
    (_dir() / f"{tid}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


async def fetch_modelfile(model: str, ollama_host: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{ollama_host}/api/show", json={"name": model})
            resp.raise_for_status()
            data = resp.json()
            return data.get("modelfile") or data.get("Modelfile")
    except Exception:
        return None


async def push_modelfile(model: str, modelfile: str, ollama_host: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{ollama_host}/api/create",
                json={"name": model, "modelfile": modelfile},
            )
            resp.raise_for_status()
            return True, "OK"
    except Exception as exc:
        return False, str(exc)


async def seed_yaml_template(ollama_host: str) -> None:
    if get_domain_template("yaml-ha-dashboard"):
        return

    from app.pipeline import HA_REFERENCE
    from app.roles import ROLE_META

    gen_mf = await fetch_modelfile("qwen-ha:1.5b", ollama_host)
    if not gen_mf:
        gen_mf = (
            "FROM qwen2.5:1.5b\n"
            'SYSTEM """You are a Home Assistant YAML expert. '
            "Output valid Lovelace YAML only. "
            'No explanations, no markdown fences."""\n'
            "PARAMETER temperature 0\n"
            "PARAMETER num_predict 1024"
        )

    rev_mf = await fetch_modelfile("gemma4:e4b", ollama_host) or (
        "FROM gemma3:4b\n"
        'SYSTEM """You are a strict YAML code reviewer. '
        "Return corrected YAML only, or REJECTED: with a list of issues. "
        'No explanations."""\n'
        "PARAMETER temperature 0\n"
        "PARAMETER num_predict 2048"
    )

    template: dict[str, Any] = {
        "id": "yaml-ha-dashboard",
        "name": "HA Dashboard YAML",
        "domain": "yaml",
        "description": "Home Assistant Lovelace dashboard generation and review pipeline",
        "pipeline": ["generator", "reviewer"],
        "stages": {
            "generator": {
                "model_name": "qwen-ha:1.5b",
                "base_model": "qwen2.5:1.5b",
                "modelfile": gen_mf,
                "persona": ROLE_META["generator"]["persona"],
                "reference": HA_REFERENCE.strip(),
                "user_prompt": "Build this: {spec}\nOutput YAML only.",
            },
            "reviewer": {
                "model_name": "gemma4:e4b",
                "base_model": "gemma3:4b",
                "modelfile": rev_mf,
                "persona": ROLE_META["reviewer"]["persona"],
                "reference": "",
                "user_prompt": (
                    "Job specification:\n{spec}\n\n"
                    "Output to review:\n{prev_output}\n\n"
                    "Return corrected YAML only. "
                    "If it cannot be fixed, write REJECTED: followed by a list of issues."
                ),
            },
        },
    }

    save_domain_template("yaml-ha-dashboard", template)
