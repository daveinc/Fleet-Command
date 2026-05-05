from __future__ import annotations

import app.config as _config

_config.load()

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from app.snapshot import capabilities, status
from app.workers import configured_workers, test_worker

app = FastAPI(title="Fleet Command")


@app.middleware("http")
async def ingress_root_path(request: Request, call_next):
    ingress_path = request.headers.get("X-Ingress-Path", "")
    if ingress_path:
        request.scope["root_path"] = ingress_path
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> str:
    root = request.scope.get("root_path", "")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <base href="{root}/">
  <title>Fleet Command</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; line-height: 1.5; }}
    code {{ background: #eceff3; padding: 0.15rem 0.3rem; }}
  </style>
</head>
<body>
  <h1>Fleet Command</h1>
  <p>Status API is running.</p>
  <p><a href="capabilities">Capabilities</a> · <a href="status">Status</a> · <a href="workers">Workers</a></p>
</body>
</html>"""


@app.get("/capabilities")
async def get_capabilities() -> dict:
    return capabilities()


@app.get("/status")
async def get_status() -> dict:
    return status()


@app.get("/workers")
async def get_workers() -> dict:
    return {"workers": configured_workers()}


@app.post("/workers/{worker_id}/test")
async def post_worker_test(worker_id: int, payload: dict | None = None) -> dict:
    prompt = "Reply with exactly: FLEET_COMMAND_WORKER_OK"
    if payload and isinstance(payload.get("prompt"), str):
        prompt = payload["prompt"]
    return await test_worker(worker_id, prompt)
