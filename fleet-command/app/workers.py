from __future__ import annotations

from typing import Any
import httpx

from app.config import options


PROVIDER_DEFAULT_PATHS = {
    "ollama": "/api/generate",
    "openai": "/v1/responses",
    "openai_compatible": "/v1/chat/completions",
    "anthropic": "/v1/messages",
    "custom_http": "",
}

PROVIDER_DEFAULT_FORMATS = {
    "ollama": "ollama_generate",
    "openai": "openai_responses",
    "openai_compatible": "openai_chat",
    "anthropic": "anthropic_messages",
    "custom_http": "raw_prompt_json",
}


def configured_workers() -> list[dict[str, Any]]:
    data = options()
    workers: list[dict[str, Any]] = []
    for index in range(1, 5):
        enabled = bool(data.get(f"worker_{index}_enabled", False))
        provider = str(data.get(f"worker_{index}_provider", "custom_http") or "custom_http")
        base_url = normalise_url(str(data.get(f"worker_{index}_base_url", "") or ""))
        api_path = str(data.get(f"worker_{index}_api_path", "") or PROVIDER_DEFAULT_PATHS.get(provider, ""))
        auth_type = str(data.get(f"worker_{index}_auth_type", "none") or "none")
        api_key = str(data.get(f"worker_{index}_api_key", "") or "")
        workers.append(
            {
                "id": index,
                "enabled": enabled,
                "name": str(data.get(f"worker_{index}_name", "") or f"Worker {index}"),
                "role": str(data.get(f"worker_{index}_role", "") or "worker"),
                "provider": provider,
                "base_url": base_url,
                "api_path": ensure_path(api_path),
                "endpoint": endpoint(base_url, api_path),
                "model": str(data.get(f"worker_{index}_model", "") or ""),
                "request_format": str(data.get(f"worker_{index}_request_format", "") or PROVIDER_DEFAULT_FORMATS.get(provider, "raw_prompt_json")),
                "auth_type": auth_type,
                "auth_header": str(data.get(f"worker_{index}_auth_header", "") or default_auth_header(auth_type)),
                "has_api_key": bool(api_key),
                "status": worker_status(enabled, base_url, auth_type, api_key),
            }
        )
    return workers


def enabled_workers() -> list[dict[str, Any]]:
    return [worker for worker in configured_workers() if worker["enabled"]]


def normalise_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        return "http://" + url
    return url


def ensure_path(path: str) -> str:
    path = path.strip()
    if path and not path.startswith("/"):
        return "/" + path
    return path


def endpoint(base_url: str, api_path: str) -> str:
    path = ensure_path(api_path)
    if not base_url:
        return ""
    return base_url.rstrip("/") + path


def default_auth_header(auth_type: str) -> str:
    if auth_type == "bearer":
        return "Authorization"
    if auth_type == "x_api_key":
        return "x-api-key"
    return ""


def worker_status(enabled: bool, base_url: str, auth_type: str, api_key: str) -> str:
    if not enabled:
        return "Disabled"
    if not base_url:
        return "Missing URL"
    if auth_type != "none" and not api_key:
        return "Missing API key"
    return "Configured"


def worker_secret(index: int) -> str:
    return str(options().get(f"worker_{index}_api_key", "") or "")


def worker_headers(worker: dict[str, Any]) -> dict[str, str]:
    secret = worker_secret(int(worker["id"]))
    if worker["auth_type"] == "none" or not secret:
        return {}
    if worker["auth_type"] == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    if worker["auth_type"] == "x_api_key":
        return {"x-api-key": secret}
    header_name = worker.get("auth_header") or "Authorization"
    return {str(header_name): secret}


def worker_payload(worker: dict[str, Any], prompt: str) -> dict[str, Any]:
    model = worker.get("model") or ""
    request_format = worker.get("request_format")
    if request_format == "ollama_generate":
        return {"model": model, "prompt": prompt, "stream": False}
    if request_format == "openai_responses":
        return {"model": model, "input": prompt}
    if request_format == "openai_chat":
        return {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if request_format == "anthropic_messages":
        return {"model": model, "max_tokens": 256, "messages": [{"role": "user", "content": prompt}]}
    return {"model": model, "prompt": prompt}


async def test_worker(worker_id: int, prompt: str = "Reply with exactly: FLEET_COMMAND_WORKER_OK") -> dict[str, Any]:
    workers = {int(worker["id"]): worker for worker in configured_workers()}
    worker = workers.get(worker_id)
    if worker is None:
        return {"ok": False, "error": f"worker not found: {worker_id}"}
    if worker["status"] != "Configured":
        return {"ok": False, "worker": worker, "error": worker["status"]}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                worker["endpoint"],
                headers=worker_headers(worker),
                json=worker_payload(worker, prompt),
            )
            text = response.text[:1000]
            return {
                "ok": response.is_success,
                "status_code": response.status_code,
                "worker": worker,
                "response_preview": text,
            }
    except Exception as err:
        return {"ok": False, "worker": worker, "error": str(err)}
