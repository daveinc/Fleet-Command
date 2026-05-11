from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

BUILTIN_HARNESSES: dict[str, dict[str, Any]] = {
    "qwen_ha_1_5b": {
        "display_name": "qwen-ha:1.5b",
        "model": "qwen-ha:1.5b",
        "endpoint": "http://host.docker.internal:11434",
        "api_path": "/api/chat",
        "request_format": "ollama_chat",
        "auth_type": "none",
        "context_window": 4096,
        "token_allowance": None,
        "cost_type": "local",
        "capabilities": ["generator"],
        "reasoning": False,
        "concurrency": 4,
        "params": {"temperature": 0},
        "notes": "YAML specialist. Proven on 15 card patterns. Custom Modelfile.",
    },
    "gemma4_e4b": {
        "display_name": "gemma4:e4b",
        "model": "gemma4:e4b",
        "endpoint": "http://host.docker.internal:11434",
        "api_path": "/api/chat",
        "request_format": "ollama_chat",
        "auth_type": "none",
        "context_window": None,
        "token_allowance": None,
        "cost_type": "local",
        "capabilities": ["manager", "reviewer"],
        "reasoning": False,
        "concurrency": 4,
        "params": {"temperature": 0},
        "notes": "Local reviewer. Proven R1/R3/R5. Matches cloud quality.",
    },
    "gpt_oss_120b_cloud": {
        "display_name": "gpt-oss:120b-cloud",
        "model": "gpt-oss:120b-cloud",
        "endpoint": "http://host.docker.internal:11434",
        "api_path": "/api/chat",
        "request_format": "ollama_chat",
        "auth_type": "none",
        "context_window": None,
        "token_allowance": None,
        "cost_type": "cloud_shared",
        "capabilities": ["project_manager", "supervisor", "reviewer"],
        "reasoning": True,
        "concurrency": 1,
        "params": {"temperature": 0},
        "notes": "Cloud. Shared token pool — use sparingly. Supports reasoning mode.",
    },
    "gemma4_31b_cloud": {
        "display_name": "gemma4:31b-cloud",
        "model": "gemma4:31b-cloud",
        "endpoint": "http://host.docker.internal:11434",
        "api_path": "/api/chat",
        "request_format": "ollama_chat",
        "auth_type": "none",
        "context_window": 128000,
        "token_allowance": None,
        "cost_type": "cloud_shared",
        "capabilities": ["reviewer", "manager"],
        "reasoning": False,
        "concurrency": 1,
        "params": {"temperature": 0},
        "notes": "Cloud. Sequential only — 500 on concurrent requests.",
    },
    "claude_sonnet": {
        "display_name": "Claude Sonnet 4.6",
        "model": "claude-sonnet-4-6",
        "endpoint": "https://api.anthropic.com",
        "api_path": "/v1/messages",
        "request_format": "anthropic_messages",
        "auth_type": "bearer",
        "context_window": 200000,
        "token_allowance": None,
        "cost_type": "cloud_metered",
        "capabilities": ["project_manager", "supervisor", "manager"],
        "reasoning": True,
        "concurrency": 10,
        "params": {"temperature": 0, "max_tokens": 4096},
        "notes": "200k context. Metered API — costs per token. Client + top-level supervisor.",
    },
    "claude_opus": {
        "display_name": "Claude Opus 4.7",
        "model": "claude-opus-4-7",
        "endpoint": "https://api.anthropic.com",
        "api_path": "/v1/messages",
        "request_format": "anthropic_messages",
        "auth_type": "bearer",
        "context_window": 200000,
        "token_allowance": None,
        "cost_type": "cloud_metered",
        "capabilities": ["project_manager", "supervisor"],
        "reasoning": True,
        "concurrency": 10,
        "params": {"temperature": 0, "max_tokens": 4096},
        "notes": "Most capable. 200k context. Highest cost — reserve for top PM role.",
    },
    "qwen2_5_coder_1_5b": {
        "display_name": "qwen2.5-coder:1.5b",
        "model": "qwen2.5-coder:1.5b",
        "endpoint": "http://host.docker.internal:11434",
        "api_path": "/api/chat",
        "request_format": "ollama_chat",
        "auth_type": "none",
        "context_window": 4096,
        "token_allowance": None,
        "cost_type": "local",
        "capabilities": ["generator"],
        "reasoning": False,
        "concurrency": 4,
        "params": {"temperature": 0},
        "notes": "General coder. Not YAML-specialized.",
    },
}

_USER_HARNESS_DIR = Path("/data/harnesses")
_OLLAMA_CTX_CACHE: dict[tuple[str, str], int | None] = {}


def _ollama_host(endpoint: str) -> str:
    try:
        from app.config import options

        configured = str(options().get("ollama_host", "") or "").strip().rstrip("/")
        if configured:
            if not configured.startswith(("http://", "https://")):
                configured = "http://" + configured
            return configured
    except Exception:
        pass
    return endpoint.rstrip("/")


def _extract_context_window(model_info: dict[str, Any]) -> int | None:
    for key, value in model_info.items():
        if key.endswith(".context_length") or key.endswith(".context_window"):
            try:
                ctx = int(value)
            except (TypeError, ValueError):
                continue
            if ctx > 0:
                return ctx
    return None


def _fetch_ollama_ctx(model: str, endpoint: str) -> int | None:
    if not model:
        return None
    host = _ollama_host(endpoint)
    cache_key = (host, model)
    if cache_key in _OLLAMA_CTX_CACHE:
        return _OLLAMA_CTX_CACHE[cache_key]

    hosts = [host]
    if "host.docker.internal" in host:
        hosts.append(host.replace("host.docker.internal", "localhost"))

    ctx = None
    for candidate in hosts:
        try:
            response = httpx.post(f"{candidate}/api/show", json={"model": model}, timeout=5)
            response.raise_for_status()
            data = response.json()
            ctx = _extract_context_window(data.get("model_info", {}))
            if ctx:
                break
        except Exception:
            continue

    _OLLAMA_CTX_CACHE[cache_key] = ctx
    return ctx


def _resolve_context_window(harness: dict[str, Any]) -> dict[str, Any]:
    if harness.get("context_window") and harness.get("context_window_source") == "manual":
        return harness
    if harness.get("request_format") not in ("ollama_chat", "ollama_generate"):
        return harness

    resolved = _fetch_ollama_ctx(
        str(harness.get("model", "") or ""),
        str(harness.get("endpoint", "") or ""),
    )
    if not resolved:
        return harness

    enriched = dict(harness)
    enriched["context_window"] = resolved
    enriched["context_window_source"] = "ollama:/api/show"
    return enriched


def load_harnesses() -> dict[str, dict[str, Any]]:
    harnesses = dict(BUILTIN_HARNESSES)
    if _USER_HARNESS_DIR.exists():
        for f in _USER_HARNESS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                harnesses[f.stem] = data
            except Exception:
                pass
    return {hid: _resolve_context_window(dict(harness)) for hid, harness in harnesses.items()}


def get_harness(harness_id: str) -> dict[str, Any] | None:
    return load_harnesses().get(harness_id)


def save_user_harness(harness_id: str, data: dict[str, Any]) -> None:
    _USER_HARNESS_DIR.mkdir(parents=True, exist_ok=True)
    (_USER_HARNESS_DIR / f"{harness_id}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
