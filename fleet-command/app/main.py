from __future__ import annotations

import app.config as _config

_config.load()

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.snapshot import capabilities, status
from app.workers import configured_workers, test_worker
from app.harnesses import load_harnesses
from app.roles import load_roles, save_roles, swap_roles, ROLE_ORDER, ROLE_LABELS, ROLE_META, ADVISOR_ROLE
from app.fleet import (
    load_fleet, save_fleet,
    add_staff, update_staff, remove_staff,
    add_project, update_project,
    add_block, update_block,
    add_task, update_task,
    fleet_counts,
    load_templates, save_templates, add_template, remove_template,
)
from app.sensor_push import push_fleet_sensors

app = FastAPI(title="Fleet Command")


@app.middleware("http")
async def ingress_root_path(request: Request, call_next):
    ingress_path = request.headers.get("X-Ingress-Path", "")
    if ingress_path:
        request.scope["root_path"] = ingress_path
    return await call_next(request)


# ── API ──────────────────────────────────────────────────────────────────────

@app.get("/api/harnesses")
async def api_harnesses() -> dict:
    return {"harnesses": load_harnesses()}


@app.get("/api/roles")
async def api_roles_get() -> dict:
    return {"roles": load_roles(), "order": ROLE_ORDER, "advisor": ADVISOR_ROLE, "meta": ROLE_META}


@app.post("/api/roles")
async def api_roles_save(payload: dict) -> dict:
    assignments = payload.get("assignments", {})
    save_roles(assignments)
    return {"ok": True}


@app.post("/api/roles/swap")
async def api_roles_swap(payload: dict) -> dict:
    role_a = payload.get("role_a")
    role_b = payload.get("role_b")
    if not role_a or not role_b:
        return JSONResponse({"ok": False, "error": "role_a and role_b required"}, status_code=400)
    assignments = load_roles()
    updated = swap_roles(assignments, role_a, role_b)
    save_roles(updated)
    return {"ok": True, "roles": updated}


@app.get("/capabilities")
async def get_capabilities() -> dict:
    return capabilities()


@app.get("/status")
async def get_status() -> dict:
    return status()


@app.get("/workers")
async def get_workers() -> dict:
    return {"workers": configured_workers()}


# ── Fleet API ────────────────────────────────────────────────────────────────

@app.get("/api/fleet")
async def api_fleet_get() -> dict:
    fleet = load_fleet()
    return {"fleet": fleet, "counts": fleet_counts(fleet)}


@app.post("/api/fleet/staff")
async def api_staff_add(payload: dict) -> dict:
    fleet = load_fleet()
    record = add_staff(fleet, payload)
    save_fleet(fleet)
    await push_fleet_sensors(fleet)
    return {"ok": True, "record": record}


@app.patch("/api/fleet/staff/{staff_id}")
async def api_staff_update(staff_id: int, payload: dict) -> dict:
    fleet = load_fleet()
    record = update_staff(fleet, staff_id, payload)
    if record is None:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    save_fleet(fleet)
    await push_fleet_sensors(fleet)
    return {"ok": True, "record": record}


@app.delete("/api/fleet/staff/{staff_id}")
async def api_staff_delete(staff_id: int) -> dict:
    fleet = load_fleet()
    removed = remove_staff(fleet, staff_id)
    if not removed:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    save_fleet(fleet)
    await push_fleet_sensors(fleet)
    return {"ok": True}


@app.post("/api/fleet/projects")
async def api_project_add(payload: dict) -> dict:
    fleet = load_fleet()
    record = add_project(fleet, payload)
    save_fleet(fleet)
    await push_fleet_sensors(fleet)
    return {"ok": True, "record": record}


@app.patch("/api/fleet/projects/{project_id}")
async def api_project_update(project_id: int, payload: dict) -> dict:
    fleet = load_fleet()
    record = update_project(fleet, project_id, payload)
    if record is None:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    save_fleet(fleet)
    await push_fleet_sensors(fleet)
    return {"ok": True, "record": record}


@app.post("/api/fleet/blocks")
async def api_block_add(payload: dict) -> dict:
    fleet = load_fleet()
    record = add_block(fleet, payload)
    save_fleet(fleet)
    await push_fleet_sensors(fleet)
    return {"ok": True, "record": record}


@app.patch("/api/fleet/blocks/{block_id}")
async def api_block_update(block_id: int, payload: dict) -> dict:
    fleet = load_fleet()
    record = update_block(fleet, block_id, payload)
    if record is None:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    save_fleet(fleet)
    await push_fleet_sensors(fleet)
    return {"ok": True, "record": record}


@app.post("/api/fleet/tasks")
async def api_task_add(payload: dict) -> dict:
    fleet = load_fleet()
    record = add_task(fleet, payload)
    save_fleet(fleet)
    await push_fleet_sensors(fleet)
    return {"ok": True, "record": record}


@app.patch("/api/fleet/tasks/{task_id}")
async def api_task_update(task_id: int, payload: dict) -> dict:
    fleet = load_fleet()
    record = update_task(fleet, task_id, payload)
    if record is None:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    save_fleet(fleet)
    await push_fleet_sensors(fleet)
    return {"ok": True, "record": record}


@app.get("/api/fleet/templates")
async def api_templates_get() -> dict:
    return {"templates": load_templates()}


@app.post("/api/fleet/templates")
async def api_templates_add(payload: dict) -> dict:
    templates = load_templates()
    record = add_template(templates, payload)
    save_templates(templates)
    return {"ok": True, "record": record}


@app.delete("/api/fleet/templates/{template_id}")
async def api_templates_delete(template_id: str) -> dict:
    templates = load_templates()
    removed = remove_template(templates, template_id)
    if not removed:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    save_templates(templates)
    return {"ok": True}


@app.post("/workers/{worker_id}/test")
async def post_worker_test(worker_id: int, payload: dict | None = None) -> dict:
    prompt = "Reply with exactly: FLEET_COMMAND_WORKER_OK"
    if payload and isinstance(payload.get("prompt"), str):
        prompt = payload["prompt"]
    return await test_worker(worker_id, prompt)


# ── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> str:
    root = request.scope.get("root_path", "").rstrip("/")
    return _dashboard_html(root)


def _dashboard_html(root: str) -> str:  # noqa: C901
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Fleet Command</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: system-ui, -apple-system, sans-serif;
      background: #111318;
      color: #e2e8f0;
      min-height: 100vh;
      padding: 1.25rem;
    }}

    /* ── Header ── */
    .header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 1.5rem;
    }}
    .header h1 {{ font-size: 1.3rem; font-weight: 600; letter-spacing: 0.02em; color: #f1f5f9; }}
    .header h1 span {{ color: #6366f1; }}

    .btn {{
      padding: 0.45rem 1rem;
      border-radius: 6px;
      border: none;
      font-size: 0.85rem;
      font-weight: 500;
      cursor: pointer;
      transition: opacity 0.15s;
    }}
    .btn:hover {{ opacity: 0.85; }}
    .btn-primary {{ background: #6366f1; color: #fff; }}
    .btn-sm {{ padding: 0.3rem 0.65rem; font-size: 0.78rem; }}
    .btn-ghost {{ background: transparent; border: 1px solid #334155; color: #94a3b8; }}
    .btn-ghost:hover {{ border-color: #6366f1; color: #6366f1; opacity: 1; }}

    /* ── Section titles ── */
    .section-title {{
      font-size: 0.7rem;
      font-weight: 600;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: #475569;
      margin-bottom: 0.75rem;
    }}

    /* ── Role cards ── */
    .roster {{ display: flex; flex-direction: column; gap: 0.6rem; margin-bottom: 1.5rem; }}

    .role-card {{
      background: #1e2330;
      border: 1px solid #2d3748;
      border-radius: 10px;
      overflow: hidden;
      transition: border-color 0.2s;
    }}
    .role-card:hover {{ border-color: #4a5568; }}
    .role-card.has-model {{ border-left: 3px solid #6366f1; }}
    .role-card.empty {{ border-left: 3px solid #374151; }}

    .card-header {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.75rem 1rem;
    }}

    .role-label-wrap {{ min-width: 130px; }}
    .role-label {{
      font-size: 0.65rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #6366f1;
    }}
    .role-title {{
      font-size: 0.68rem;
      color: #475569;
      margin-top: 0.1rem;
    }}

    .model-select {{
      flex: 1;
      background: #0f1117;
      border: 1px solid #334155;
      border-radius: 6px;
      color: #e2e8f0;
      font-size: 0.85rem;
      padding: 0.35rem 0.6rem;
      cursor: pointer;
      outline: none;
    }}
    .model-select:focus {{ border-color: #6366f1; }}

    .cost-badge {{
      font-size: 0.7rem;
      padding: 0.2rem 0.5rem;
      border-radius: 4px;
      font-weight: 500;
      white-space: nowrap;
    }}
    .local {{ background: #052e16; color: #4ade80; }}
    .cloud {{ background: #1c1917; color: #fbbf24; }}
    .unset {{ background: #1e293b; color: #475569; }}

    .card-actions {{
      display: flex;
      gap: 0.3rem;
      align-items: center;
    }}

    .card-meta {{
      padding: 0 1rem 0.65rem 1rem;
      display: flex;
      align-items: center;
      gap: 1.25rem;
      font-size: 0.75rem;
      color: #64748b;
    }}
    .card-meta span {{ display: flex; align-items: center; gap: 0.3rem; }}

    /* context bar */
    .ctx-bar-wrap {{
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }}
    .ctx-bar {{
      width: 80px;
      height: 5px;
      background: #1e293b;
      border-radius: 3px;
      overflow: hidden;
    }}
    .ctx-bar-fill {{
      height: 100%;
      background: #6366f1;
      border-radius: 3px;
      transition: width 0.3s;
    }}

    /* params panel */
    .params-panel {{
      display: none;
      padding: 0.6rem 1rem 0.75rem;
      border-top: 1px solid #1e293b;
      background: #171c28;
    }}
    .params-panel.open {{ display: block; }}
    .params-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 0.5rem;
    }}
    .param-row {{ display: flex; flex-direction: column; gap: 0.2rem; }}
    .param-row label {{ font-size: 0.7rem; color: #64748b; }}
    .param-row input {{
      background: #0f1117;
      border: 1px solid #334155;
      border-radius: 4px;
      color: #e2e8f0;
      font-size: 0.82rem;
      padding: 0.25rem 0.4rem;
      width: 100%;
      outline: none;
    }}
    .param-row input:focus {{ border-color: #6366f1; }}

    /* ── Available pool ── */
    .pool {{ display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 1.5rem; }}
    .pool-chip {{
      background: #1e2330;
      border: 1px solid #2d3748;
      border-radius: 6px;
      padding: 0.35rem 0.7rem;
      font-size: 0.78rem;
      color: #94a3b8;
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }}
    .pool-chip .chip-ctx {{ font-size: 0.68rem; color: #475569; }}

    /* ── Job monitor ── */
    .job-monitor {{
      background: #1e2330;
      border: 1px solid #2d3748;
      border-radius: 10px;
      padding: 1rem;
    }}
    .job-status-row {{
      display: flex;
      align-items: center;
      gap: 1rem;
      margin-bottom: 0.75rem;
    }}
    .status-dot {{
      width: 8px; height: 8px;
      border-radius: 50%;
      background: #374151;
    }}
    .status-dot.active {{ background: #4ade80; box-shadow: 0 0 6px #4ade80; }}
    .job-id {{ font-size: 0.82rem; color: #94a3b8; }}
    .job-progress {{ font-size: 0.78rem; color: #64748b; margin-left: auto; }}

    .progress-bar-wrap {{
      height: 4px;
      background: #1e293b;
      border-radius: 2px;
      margin-bottom: 0.75rem;
      overflow: hidden;
    }}
    .progress-bar-fill {{
      height: 100%;
      background: linear-gradient(90deg, #6366f1, #818cf8);
      border-radius: 2px;
      width: 0%;
      transition: width 0.4s;
    }}

    .worker-row-grid {{
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
      margin-bottom: 0.75rem;
    }}
    .worker-pill {{
      font-size: 0.72rem;
      padding: 0.2rem 0.5rem;
      border-radius: 4px;
      background: #0f1117;
      border: 1px solid #2d3748;
      color: #64748b;
    }}
    .worker-pill.busy {{ border-color: #6366f1; color: #818cf8; }}

    .log-area {{
      font-size: 0.72rem;
      font-family: 'Fira Mono', 'Courier New', monospace;
      color: #475569;
      max-height: 140px;
      overflow-y: auto;
      line-height: 1.7;
    }}
    .log-area .log-line {{ color: #64748b; }}
    .log-area .log-line .ts {{ color: #334155; margin-right: 0.4rem; }}
    .log-idle {{ color: #374151; font-style: italic; }}

    /* ── Tabs ── */
    .tabs {{
      display: flex;
      gap: 0;
      border-bottom: 1px solid #1e293b;
      margin-bottom: 1.25rem;
    }}
    .tab {{
      padding: 0.5rem 1.1rem;
      font-size: 0.82rem;
      font-weight: 500;
      color: #475569;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      margin-bottom: -1px;
      transition: color 0.15s, border-color 0.15s;
    }}
    .tab:hover {{ color: #94a3b8; }}
    .tab.active {{ color: #6366f1; border-bottom-color: #6366f1; }}

    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}

    /* ── Save bar ── */
    .save-bar {{
      position: sticky;
      bottom: 0;
      background: #111318;
      padding: 0.75rem 0 0;
      display: flex;
      justify-content: flex-end;
      gap: 0.5rem;
      border-top: 1px solid #1e293b;
      margin-top: 1rem;
    }}
    .save-feedback {{
      font-size: 0.78rem;
      color: #4ade80;
      align-self: center;
      opacity: 0;
      transition: opacity 0.3s;
    }}
    .save-feedback.show {{ opacity: 1; }}

    /* ── Harness cards ── */
    .harness-grid {{ display: flex; flex-direction: column; gap: 0.6rem; }}
    .harness-card {{
      background: #1e2330;
      border: 1px solid #2d3748;
      border-radius: 10px;
      padding: 0.75rem 1rem;
      display: flex;
      align-items: flex-start;
      gap: 1rem;
    }}
    .harness-info {{ flex: 1; }}
    .harness-name {{ font-size: 0.88rem; font-weight: 600; color: #e2e8f0; margin-bottom: 0.2rem; }}
    .harness-meta {{ font-size: 0.72rem; color: #475569; display: flex; gap: 0.75rem; flex-wrap: wrap; }}
    .harness-notes {{ font-size: 0.72rem; color: #374151; margin-top: 0.25rem; }}
    .cap-tag {{
      font-size: 0.66rem;
      padding: 0.15rem 0.4rem;
      border-radius: 3px;
      background: #1e293b;
      color: #6366f1;
    }}

    /* ── Template cards ── */
    .tmpl-grid {{ display: grid; grid-template-columns: repeat(auto-fill,minmax(260px,1fr)); gap: 0.75rem; }}
    .tmpl-card {{
      background: #1e2330; border: 1px solid #2d3748; border-radius: 10px;
      padding: 0.85rem 1rem; display: flex; flex-direction: column; gap: 0.4rem;
    }}
    .tmpl-name {{ font-size: 0.9rem; font-weight: 600; color: #e2e8f0; }}
    .tmpl-desc {{ font-size: 0.75rem; color: #475569; flex: 1; }}
    .tmpl-foot {{ display: flex; align-items: center; gap: 0.5rem; margin-top: 0.4rem; }}
    .tmpl-type {{
      font-size: 0.66rem; padding: 0.15rem 0.45rem; border-radius: 3px;
      background: #1e293b; color: #818cf8; font-weight: 500;
    }}

    /* ── Staff cards ── */
    .staff-grid {{ display: flex; flex-direction: column; gap: 0.55rem; }}
    .staff-card {{
      background: #1e2330;
      border: 1px solid #2d3748;
      border-radius: 10px;
      padding: 0.75rem 1rem;
      display: flex;
      gap: 1rem;
      align-items: flex-start;
    }}
    .staff-card.hr {{ border-left: 3px solid #38bdf8; }}
    .staff-card.ar {{ border-left: 3px solid #a78bfa; }}
    .staff-avatar {{
      width: 36px; height: 36px;
      border-radius: 50%;
      display: flex; align-items: center; justify-content: center;
      font-size: 1rem;
      flex-shrink: 0;
    }}
    .staff-avatar.hr {{ background: #0c2233; color: #38bdf8; }}
    .staff-avatar.ar {{ background: #1a1033; color: #a78bfa; }}
    .staff-body {{ flex: 1; min-width: 0; }}
    .staff-name {{ font-size: 0.9rem; font-weight: 600; color: #e2e8f0; }}
    .staff-role {{ font-size: 0.7rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.06em; }}
    .staff-meta {{ display: flex; gap: 0.75rem; flex-wrap: wrap; margin-top: 0.3rem; font-size: 0.72rem; color: #475569; }}
    .staff-status {{
      font-size: 0.68rem; padding: 0.15rem 0.45rem; border-radius: 3px; font-weight: 500;
    }}
    .staff-status.available {{ background: #052e16; color: #4ade80; }}
    .staff-status.busy {{ background: #1c1917; color: #fbbf24; }}
    .staff-status.vacation {{ background: #1e293b; color: #475569; }}
    .staff-tasks {{ margin-top: 0.4rem; font-size: 0.72rem; color: #334155; }}
    .staff-tasks li {{ margin-left: 1rem; margin-top: 0.1rem; }}

    /* ── Project / block / task cards ── */
    .project-card {{
      background: #1e2330;
      border: 1px solid #2d3748;
      border-radius: 10px;
      margin-bottom: 0.75rem;
      overflow: hidden;
    }}
    .project-header {{
      padding: 0.75rem 1rem;
      display: flex;
      align-items: center;
      gap: 0.75rem;
      cursor: pointer;
      user-select: none;
    }}
    .project-header:hover {{ background: #1a2030; }}
    .project-name {{ font-size: 0.9rem; font-weight: 600; color: #e2e8f0; flex: 1; }}
    .project-body {{ padding: 0 1rem 0.75rem; }}
    .project-meta {{ font-size: 0.72rem; color: #475569; display: flex; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 0.5rem; }}

    .prog-wrap {{ margin: 0.4rem 0 0.6rem; }}
    .prog-label {{ display: flex; justify-content: space-between; font-size: 0.7rem; color: #475569; margin-bottom: 0.2rem; }}
    .prog-bar {{ height: 5px; background: #1e293b; border-radius: 3px; overflow: hidden; }}
    .prog-fill {{ height: 100%; border-radius: 3px; background: linear-gradient(90deg,#6366f1,#818cf8); transition: width 0.3s; }}

    .status-badge {{
      font-size: 0.68rem; padding: 0.15rem 0.5rem; border-radius: 3px; font-weight: 500; white-space: nowrap;
    }}
    .status-ongoing {{ background: #052e16; color: #4ade80; }}
    .status-done {{ background: #1e293b; color: #475569; }}
    .status-halted {{ background: #2d1515; color: #f87171; }}
    .status-pending {{ background: #1c1917; color: #fbbf24; }}

    .block-list {{ display: flex; flex-direction: column; gap: 0.45rem; margin-top: 0.5rem; }}
    .block-card {{
      background: #171c28;
      border: 1px solid #243040;
      border-radius: 7px;
      padding: 0.55rem 0.75rem;
    }}
    .block-name {{ font-size: 0.8rem; font-weight: 500; color: #cbd5e1; }}
    .block-meta {{ font-size: 0.7rem; color: #475569; display: flex; gap: 0.6rem; flex-wrap: wrap; margin-top: 0.2rem; }}

    .task-list {{ margin-top: 0.4rem; display: flex; flex-direction: column; gap: 0.3rem; }}
    .task-row {{
      display: flex; align-items: center; gap: 0.5rem;
      font-size: 0.72rem; color: #475569;
      padding: 0.2rem 0;
    }}
    .task-num {{ color: #334155; min-width: 1.5rem; }}
    .task-name {{ flex: 1; }}
    .task-worker {{ color: #374151; font-size: 0.68rem; }}

    /* ── Modal ── */
    .modal-overlay {{
      display: none; position: fixed; inset: 0;
      background: rgba(0,0,0,0.6); z-index: 100;
      align-items: center; justify-content: center;
    }}
    .modal-overlay.open {{ display: flex; }}
    .modal {{
      background: #1e2330; border: 1px solid #334155; border-radius: 12px;
      padding: 1.25rem; width: min(420px, 90vw); display: flex; flex-direction: column; gap: 0.75rem;
    }}
    .modal h3 {{ font-size: 0.95rem; font-weight: 600; color: #e2e8f0; }}
    .field {{ display: flex; flex-direction: column; gap: 0.2rem; }}
    .field label {{ font-size: 0.72rem; color: #64748b; }}
    .field input, .field select, .field textarea {{
      background: #0f1117; border: 1px solid #334155; border-radius: 5px;
      color: #e2e8f0; font-size: 0.85rem; padding: 0.35rem 0.5rem; outline: none; width: 100%;
    }}
    .field input:focus, .field select:focus {{ border-color: #6366f1; }}
    .modal-actions {{ display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 0.25rem; }}
  </style>
</head>
<body>

<div class="header">
  <h1>Fleet <span>Command</span></h1>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('fleet', this)">Fleet</div>
  <div class="tab" onclick="switchTab('staff', this)">Staff</div>
  <div class="tab" onclick="switchTab('jobs', this)">Projects</div>
  <div class="tab" onclick="switchTab('harnesses', this)">Harnesses</div>
  <div class="tab" onclick="switchTab('templates', this)">Templates</div>
</div>

<!-- ── Fleet tab ── -->
<div class="tab-panel active" id="tab-fleet">
  <div class="section-title">Chief Advisor — On-Call Escalation</div>
  <div id="advisor-card"></div>

  <div style="text-align:center;color:#334155;font-size:0.7rem;padding:0.3rem 0 0.6rem">↕ escalation only</div>

  <div class="section-title">Production Chain — Top Authority → Worker</div>
  <div class="roster" id="roster"></div>

  <div class="section-title">Available (unassigned)</div>
  <div class="pool" id="pool"></div>

  <div class="section-title">Active Job</div>
  <div class="job-monitor">
    <div class="job-status-row">
      <div class="status-dot" id="status-dot"></div>
      <div class="job-id" id="job-id">No active job</div>
      <div class="job-progress" id="job-progress"></div>
    </div>
    <div class="progress-bar-wrap"><div class="progress-bar-fill" id="progress-bar"></div></div>
    <div class="worker-row-grid" id="worker-pills"></div>
    <div class="log-area" id="log-area">
      <div class="log-idle">Fleet is idle.</div>
    </div>
  </div>

  <div class="save-bar">
    <span class="save-feedback" id="save-feedback">Chain saved</span>
    <button class="btn btn-ghost btn-sm" onclick="resetChain()">Reset</button>
    <button class="btn btn-primary" onclick="saveChain()">Save Chain</button>
  </div>
</div>

<!-- ── Staff tab ── -->
<div class="tab-panel" id="tab-staff">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem">
    <div class="section-title" style="margin:0">Human Resources</div>
    <button class="btn btn-ghost btn-sm" onclick="openAddStaff('HR')">+ Add</button>
  </div>
  <div class="staff-grid" id="staff-hr"></div>

  <div style="display:flex;justify-content:space-between;align-items:center;margin:1.25rem 0 0.75rem">
    <div class="section-title" style="margin:0">AI Resources</div>
    <button class="btn btn-ghost btn-sm" onclick="openAddStaff('AR')">+ Add</button>
  </div>
  <div class="staff-grid" id="staff-ar"></div>
</div>

<!-- ── Jobs tab ── -->
<div class="tab-panel" id="tab-jobs">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem">
    <div class="section-title" style="margin:0">Projects</div>
    <button class="btn btn-ghost btn-sm" onclick="openAddProject()">+ New Project</button>
  </div>
  <div id="projects-list"></div>
</div>

<!-- ── Harnesses tab ── -->
<div class="tab-panel" id="tab-harnesses">
  <div class="section-title">Registered Models</div>
  <div class="harness-grid" id="harness-grid"></div>
</div>

<!-- ── Templates tab ── -->
<div class="tab-panel" id="tab-templates">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem">
    <div class="section-title" style="margin:0">Project Templates</div>
    <button class="btn btn-ghost btn-sm" onclick="openAddTemplate()">+ New Template</button>
  </div>
  <div class="tmpl-grid" id="tmpl-grid"></div>
</div>

<!-- ── Modals ── -->
<div class="modal-overlay" id="modal-staff">
  <div class="modal">
    <h3 id="modal-staff-title">Add Staff</h3>
    <div class="field"><label>Name</label><input id="sf-name" placeholder="Name"></div>
    <div class="field"><label>Role</label><input id="sf-role" placeholder="e.g. Developer, Reviewer"></div>
    <div class="field"><label>Budget</label><input id="sf-budget" placeholder="e.g. Infinite, Electricity"></div>
    <div class="field">
      <label>Status</label>
      <select id="sf-status">
        <option>Available</option><option>Busy</option><option>On Vacation</option><option>Unavailable</option>
      </select>
    </div>
    <div class="field"><label>Score (e.g. 9/10)</label><input id="sf-score" placeholder="—"></div>
    <div class="field"><label>Profile / Notes</label><textarea id="sf-profile" rows="2" style="resize:vertical"></textarea></div>
    <div class="modal-actions">
      <button class="btn btn-ghost btn-sm" onclick="closeModal('modal-staff')">Cancel</button>
      <button class="btn btn-primary btn-sm" onclick="submitStaff()">Add</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="modal-template">
  <div class="modal">
    <h3>New Template</h3>
    <div class="field"><label>Name</label><input id="tm-name" placeholder="e.g. HA Dashboard Builder"></div>
    <div class="field">
      <label>Type</label>
      <select id="tm-type">
        <option value="ha_dashboard">HA Dashboard</option>
        <option value="python_addon">Python Addon</option>
        <option value="yaml_config">YAML Config</option>
        <option value="code_project">Code Project</option>
        <option value="custom">Custom</option>
      </select>
    </div>
    <div class="field"><label>Description</label><textarea id="tm-desc" rows="3" style="resize:vertical" placeholder="What does this template produce?"></textarea></div>
    <div class="field"><label>Author</label><input id="tm-author" placeholder="e.g. claude, david"></div>
    <div class="modal-actions">
      <button class="btn btn-ghost btn-sm" onclick="closeModal('modal-template')">Cancel</button>
      <button class="btn btn-primary btn-sm" onclick="submitTemplate()">Create</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="modal-project">
  <div class="modal">
    <h3>New Project</h3>
    <div class="field"><label>Name</label><input id="pj-name" placeholder="Project name"></div>
    <div class="field"><label>Budget</label><input id="pj-budget" placeholder="e.g. Full time"></div>
    <div class="field">
      <label>Status</label>
      <select id="pj-status">
        <option>Ongoing</option><option>Halted</option><option>Done</option>
      </select>
    </div>
    <div class="field"><label>Progress (current/total e.g. 5/50)</label><input id="pj-progress" placeholder="0/100"></div>
    <div class="field"><label>Notes</label><textarea id="pj-notes" rows="2" style="resize:vertical"></textarea></div>
    <div class="modal-actions">
      <button class="btn btn-ghost btn-sm" onclick="closeModal('modal-project')">Cancel</button>
      <button class="btn btn-primary btn-sm" onclick="submitProject()">Create</button>
    </div>
  </div>
</div>

<script>
const ROOT = "{root}";
const api = path => ROOT + path;

let harnesses = {{}};
let roles = {{}};
let roleMeta = {{}};
let roleOrder = [];
let advisorRole = "advisor";
let originalRoles = {{}};
let fleetData = {{ staff: [], projects: [], blocks: [], tasks: [] }};
let configuredWorkers = [];

async function load() {{
  const [hRes, rRes, fRes, wRes] = await Promise.all([
    fetch(api("/api/harnesses")).then(r => r.json()),
    fetch(api("/api/roles")).then(r => r.json()),
    fetch(api("/api/fleet")).then(r => r.json()),
    fetch(api("/workers")).then(r => r.json()),
  ]);
  harnesses = hRes.harnesses || {{}};
  roles = rRes.roles || {{}};
  roleMeta = rRes.meta || {{}};
  roleOrder = rRes.order || [];
  advisorRole = rRes.advisor || "advisor";
  originalRoles = JSON.parse(JSON.stringify(roles));
  fleetData = fRes.fleet || {{ staff: [], projects: [], blocks: [], tasks: [] }};
  configuredWorkers = (wRes.workers || []).filter(w => w.enabled);
  renderRoster();
  renderPool();
  renderHarnesses();
  renderStaff();
  renderProjects();
  loadTemplates();
}}

function ctxLabel(h) {{
  if (!h) return "—";
  if (h.context_window) {{
    const k = h.context_window >= 1000 ? (h.context_window / 1000).toFixed(0) + "k" : h.context_window;
    return "ctx " + k;
  }}
  return "ctx ?";
}}

function costBadge(h) {{
  if (!h) return '<span class="cost-badge unset">unset</span>';
  if (h.cost_type === "local") return '<span class="cost-badge local">local</span>';
  return '<span class="cost-badge cloud">cloud</span>';
}}

function roleCard(role, idx, list) {{
  const assignment = roles[role] || {{}};
  const hid = assignment.harness_id || "";
  const h = harnesses[hid];
  const meta = roleMeta[role] || {{}};
  const params = assignment.params || {{}};
  const temp = params.temperature ?? (h?.params?.temperature ?? 0);
  const hasModel = !!hid;

  const options = Object.entries(harnesses)
    .map(([id, info]) => `<option value="${{id}}" ${{id === hid ? "selected" : ""}}>${{info.display_name}}</option>`)
    .join("");

  const upBtn = idx > 0
    ? `<button class="btn btn-ghost btn-sm" onclick="swapRoles('${{role}}','${{list[idx-1]}}')" title="Promote">↑</button>`
    : "";
  const downBtn = idx < list.length - 1
    ? `<button class="btn btn-ghost btn-sm" onclick="swapRoles('${{role}}','${{list[idx+1]}}')" title="Demote">↓</button>`
    : "";

  return `
  <div class="role-card ${{hasModel ? "has-model" : "empty"}}" id="card-${{role}}">
    <div class="card-header">
      <div class="role-label-wrap">
        <div class="role-label">${{meta.label || role}}</div>
        <div class="role-title">${{meta.title || ""}}</div>
      </div>
      <select class="model-select" onchange="onModelChange('${{role}}', this.value)">
        <option value="">— unassigned —</option>
        ${{options}}
      </select>
      ${{costBadge(h)}}
      <div class="card-actions">
        <button class="btn btn-ghost btn-sm" onclick="toggleParams('${{role}}')" title="Params">⚙</button>
        ${{upBtn}}${{downBtn}}
      </div>
    </div>
    <div class="card-meta">
      <span>${{ctxLabel(h)}}</span>
      <span>temp <b>${{temp}}</b></span>
      ${{meta.description ? `<span style="color:#374151;font-style:italic">${{meta.description}}</span>` : ""}}
    </div>
    <div class="params-panel" id="params-${{role}}">
      <div class="params-grid">
        <div class="param-row">
          <label>temperature</label>
          <input type="number" min="0" max="2" step="0.1" value="${{temp}}"
            onchange="onParamChange('${{role}}', 'temperature', parseFloat(this.value))">
        </div>
        <div class="param-row">
          <label>top_p</label>
          <input type="number" min="0" max="1" step="0.05" value="${{params.top_p ?? ""}}" placeholder="default"
            onchange="onParamChange('${{role}}', 'top_p', parseFloat(this.value) || null)">
        </div>
        <div class="param-row">
          <label>top_k</label>
          <input type="number" min="0" step="1" value="${{params.top_k ?? ""}}" placeholder="default"
            onchange="onParamChange('${{role}}', 'top_k', parseInt(this.value) || null)">
        </div>
        <div class="param-row">
          <label>num_predict</label>
          <input type="number" min="0" step="64" value="${{params.num_predict ?? ""}}" placeholder="default"
            onchange="onParamChange('${{role}}', 'num_predict', parseInt(this.value) || null)">
        </div>
      </div>
    </div>
  </div>`;
}}

function renderRoster() {{
  const el = document.getElementById("roster");
  const advisorEl = document.getElementById("advisor-card");

  el.innerHTML = roleOrder.map((role, idx) => roleCard(role, idx, roleOrder)).join(
    `<div style="text-align:center;color:#334155;font-size:0.7rem;padding:0.1rem 0">↓</div>`
  );

  if (advisorEl) {{
    advisorEl.innerHTML = roleCard(advisorRole, 0, [advisorRole]);
  }}
}}

function renderPool() {{
  const el = document.getElementById("pool");
  const assignedIds = new Set(
    ROLE_ORDER.map(r => roles[r]?.harness_id).filter(Boolean)
  );
  const unassigned = Object.entries(harnesses).filter(([id]) => !assignedIds.has(id));
  if (unassigned.length === 0) {{
    el.innerHTML = '<span style="font-size:0.78rem;color:#374151">All models assigned to roles.</span>';
    return;
  }}
  el.innerHTML = unassigned.map(([id, h]) => `
    <div class="pool-chip">
      <span>${{h.display_name}}</span>
      <span class="chip-ctx">${{ctxLabel(h)}}</span>
      ${{costBadge(h)}}
    </div>`).join("");
}}

function onModelChange(role, harnessId) {{
  if (!roles[role]) roles[role] = {{ harness_id: null, params: {{}} }};
  roles[role].harness_id = harnessId || null;
  renderRoster();
  renderPool();
}}

function onParamChange(role, key, value) {{
  if (!roles[role]) roles[role] = {{ harness_id: null, params: {{}} }};
  if (!roles[role].params) roles[role].params = {{}};
  if (value === null || isNaN(value)) delete roles[role].params[key];
  else roles[role].params[key] = value;
}}

function toggleParams(role) {{
  const panel = document.getElementById("params-" + role);
  panel.classList.toggle("open");
}}

async function swapRoles(roleA, roleB) {{
  await fetch(api("/api/roles/swap"), {{
    method: "POST",
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify({{ role_a: roleA, role_b: roleB }}),
  }});
  await load();
}}

async function saveChain() {{
  await fetch(api("/api/roles"), {{
    method: "POST",
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify({{ assignments: roles }}),
  }});
  originalRoles = JSON.parse(JSON.stringify(roles));
  const fb = document.getElementById("save-feedback");
  fb.classList.add("show");
  setTimeout(() => fb.classList.remove("show"), 2000);
}}

async function resetChain() {{
  roles = JSON.parse(JSON.stringify(originalRoles));
  renderRoster();
  renderPool();
}}

function switchTab(name, el) {{
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
  el.classList.add("active");
  document.getElementById("tab-" + name).classList.add("active");
  if (name === "harnesses") renderHarnesses();
}}

function renderHarnesses() {{
  const el = document.getElementById("harness-grid");
  el.innerHTML = Object.entries(harnesses).map(([id, h]) => {{
    const ctx = h.context_window ? (h.context_window >= 1000 ? (h.context_window/1000).toFixed(0)+"k" : h.context_window) : "?";
    const caps = (h.capabilities || []).map(c => `<span class="cap-tag">${{c}}</span>`).join(" ");
    const costBadgeHtml = h.cost_type === "local"
      ? '<span class="cost-badge local">local</span>'
      : h.cost_type === "cloud_metered"
        ? '<span class="cost-badge cloud">metered</span>'
        : '<span class="cost-badge cloud">cloud</span>';
    return `
    <div class="harness-card">
      <div class="harness-info">
        <div class="harness-name">${{h.display_name}}</div>
        <div class="harness-meta">
          ${{costBadgeHtml}}
          <span>ctx ${{ctx}}</span>
          <span>temp ${{h.params?.temperature ?? "?"}}</span>
          <span>concurrency ${{h.concurrency ?? "?"}}</span>
          ${{h.reasoning ? '<span style="color:#818cf8">reasoning ✓</span>' : ""}}
        </div>
        <div style="margin-top:0.35rem;display:flex;gap:0.3rem;flex-wrap:wrap">${{caps}}</div>
        ${{h.notes ? `<div class="harness-notes">${{h.notes}}</div>` : ""}}
      </div>
    </div>`;
  }}).join("");
}}

// ── Staff ──────────────────────────────────────────────────────────────────

function staffStatusClass(status) {{
  const s = (status || "").toLowerCase();
  if (s === "available") return "available";
  if (s === "busy" || s.includes("progress")) return "busy";
  return "vacation";
}}

function staffCard(s) {{
  const type = (s.type || "HR").toLowerCase();
  const statusCls = staffStatusClass(s.status);
  const icon = type === "hr" ? "👤" : "🤖";
  const tasks = (s.assigned_work || []);
  const taskHtml = tasks.length
    ? `<ul class="staff-tasks">${{tasks.map(t => `<li>${{t}}</li>`).join("")}}</ul>`
    : "";
  return `
  <div class="staff-card ${{type}}" data-id="${{s.id}}">
    <div class="staff-avatar ${{type}}">${{icon}}</div>
    <div class="staff-body">
      <div style="display:flex;align-items:center;gap:0.5rem">
        <span class="staff-name">${{s.name || "Unnamed"}}</span>
        <span class="staff-status ${{statusCls}}">${{s.status || "—"}}</span>
      </div>
      <div class="staff-role">${{s.role || "—"}}</div>
      <div class="staff-meta">
        ${{s.score ? `<span>⭐ ${{s.score}}</span>` : ""}}
        ${{s.budget ? `<span>💰 ${{s.budget}}</span>` : ""}}
        ${{s.hired ? `<span>📅 ${{s.hired}}</span>` : ""}}
      </div>
      ${{s.profile ? `<div style="font-size:0.72rem;color:#334155;margin-top:0.25rem;font-style:italic">${{s.profile}}</div>` : ""}}
      ${{taskHtml}}
    </div>
    ${{s._readonly ? "" : `<button class="btn btn-ghost btn-sm" onclick="deleteStaff(${{s.id}})" title="Remove" style="font-size:0.7rem;color:#374151">✕</button>`}}
  </div>`;
}}

function workerToStaff(w) {{
  const statusMap = {{
    "Configured": "Available",
    "Disabled": "On Vacation",
    "Missing URL": "Unavailable",
    "Missing API key": "Unavailable",
  }};
  return {{
    id: "w" + w.id,
    type: "AR",
    name: w.name || ("Worker " + w.id),
    role: w.role || "worker",
    budget: w.provider || "—",
    status: statusMap[w.status] || w.status,
    score: "",
    profile: [w.model, w.request_format, w.endpoint].filter(Boolean).join(" · "),
    assigned_work: [],
    _readonly: true,
  }};
}}

function renderStaff() {{
  const hr = fleetData.staff.filter(s => s.type === "HR");
  const arManual = fleetData.staff.filter(s => s.type === "AR");
  const arWorkers = configuredWorkers.map(workerToStaff);
  const ar = [...arWorkers, ...arManual];

  document.getElementById("staff-hr").innerHTML = hr.length
    ? hr.map(staffCard).join("") : '<div style="color:#374151;font-size:0.8rem;padding:0.5rem 0">No human staff yet.</div>';
  document.getElementById("staff-ar").innerHTML = ar.length
    ? ar.map(staffCard).join("") : '<div style="color:#374151;font-size:0.8rem;padding:0.5rem 0">No AI staff yet.</div>';
}}

let _addStaffType = "HR";

function openAddStaff(type) {{
  _addStaffType = type;
  document.getElementById("modal-staff-title").textContent = type === "HR" ? "Add Human Staff" : "Add AI Staff";
  ["sf-name","sf-role","sf-budget","sf-score","sf-profile"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("sf-status").value = "Available";
  document.getElementById("modal-staff").classList.add("open");
}}

async function submitStaff() {{
  const record = {{
    type: _addStaffType,
    name: document.getElementById("sf-name").value.trim(),
    role: document.getElementById("sf-role").value.trim(),
    budget: document.getElementById("sf-budget").value.trim(),
    status: document.getElementById("sf-status").value,
    score: document.getElementById("sf-score").value.trim(),
    profile: document.getElementById("sf-profile").value.trim(),
    assigned_work: [],
  }};
  const res = await fetch(api("/api/fleet/staff"), {{
    method: "POST", headers: {{"Content-Type":"application/json"}}, body: JSON.stringify(record),
  }}).then(r => r.json());
  if (res.ok) {{
    fleetData.staff.push(res.record);
    renderStaff();
    closeModal("modal-staff");
  }}
}}

async function deleteStaff(id) {{
  if (!confirm("Remove this staff member?")) return;
  const res = await fetch(api("/api/fleet/staff/" + id), {{ method: "DELETE" }}).then(r => r.json());
  if (res.ok) {{
    fleetData.staff = fleetData.staff.filter(s => s.id !== id);
    renderStaff();
  }}
}}

// ── Projects ───────────────────────────────────────────────────────────────

function parseProgress(str) {{
  if (!str) return [0, 100];
  const parts = String(str).split("/");
  return [parseInt(parts[0]) || 0, parseInt(parts[1]) || 100];
}}

function progressBar(cur, total) {{
  const pct = total > 0 ? Math.min(100, Math.round(cur / total * 100)) : 0;
  return `
  <div class="prog-wrap">
    <div class="prog-label"><span>Progress</span><span>${{cur}}/${{total}} (${{pct}}%)</span></div>
    <div class="prog-bar"><div class="prog-fill" style="width:${{pct}}%"></div></div>
  </div>`;
}}

function statusBadge(status) {{
  const s = (status || "").toLowerCase();
  const cls = s.includes("ongoing") || s.includes("progress") || s.includes("active") ? "ongoing"
    : s === "done" ? "done"
    : s === "halted" ? "halted"
    : "pending";
  return `<span class="status-badge status-${{cls}}">${{status || "—"}}</span>`;
}}

function projectCard(p) {{
  const [cur, total] = parseProgress(p.progress_str || (p.progress !== undefined ? `${{p.progress}}/${{p.progress_total || 100}}` : "0/100"));
  const blocks = fleetData.blocks.filter(b => b.project_id === p.id);
  const tasks = fleetData.tasks.filter(t => t.project_id === p.id);

  const blockHtml = blocks.map(b => {{
    const [bc, bt] = parseProgress(b.progress_str || `${{b.progress || 0}}/${{b.progress_total || 100}}`);
    const bTasks = fleetData.tasks.filter(t => t.block_id === b.id);
    const taskRows = bTasks.map((t, i) => `
      <div class="task-row">
        <span class="task-num">#${{t.id}}</span>
        <span class="task-name">${{t.name || "—"}}</span>
        ${{statusBadge(t.status)}}
        <span class="task-worker">${{t.worker || ""}}</span>
      </div>`).join("");
    return `
    <div class="block-card">
      <div style="display:flex;align-items:center;gap:0.5rem">
        <span class="block-name">${{b.name || "Block " + b.id}}</span>
        ${{statusBadge(b.status)}}
      </div>
      <div class="block-meta">
        ${{b.task_current !== undefined ? `<span>Task ${{b.task_current}}/${{b.task_total || "?"}}</span>` : ""}}
        ${{b.worker ? `<span>${{b.worker}}</span>` : ""}}
      </div>
      ${{progressBar(bc, bt)}}
      ${{bTasks.length ? `<div class="task-list">${{taskRows}}</div>` : ""}}
    </div>`;
  }}).join("");

  const looseTasks = tasks.filter(t => !t.block_id);
  const looseRows = looseTasks.map(t => `
    <div class="task-row">
      <span class="task-num">#${{t.id}}</span>
      <span class="task-name">${{t.name || "—"}}</span>
      ${{statusBadge(t.status)}}
      <span class="task-worker">${{t.worker || ""}}</span>
    </div>`).join("");

  return `
  <div class="project-card">
    <div class="project-header" onclick="this.parentElement.classList.toggle('open')">
      <span class="project-name">${{p.name || "Project " + p.id}}</span>
      ${{statusBadge(p.status)}}
      <span style="font-size:0.72rem;color:#475569">${{cur}}/${{total}}</span>
      <span style="color:#475569;font-size:0.8rem;margin-left:auto">▾</span>
    </div>
    <div class="project-body" style="display:none">
      <div class="project-meta">
        ${{p.budget ? `<span>💰 ${{p.budget}}</span>` : ""}}
        ${{p.started ? `<span>📅 ${{p.started}}</span>` : ""}}
        ${{p.estimated_completion ? `<span>⏱ ${{p.estimated_completion}}</span>` : ""}}
      </div>
      ${{progressBar(cur, total)}}
      ${{blocks.length ? `<div class="block-list">${{blockHtml}}</div>` : ""}}
      ${{looseTasks.length ? `<div class="task-list" style="margin-top:0.5rem">${{looseRows}}</div>` : ""}}
      ${{!blocks.length && !looseTasks.length ? `<div style="font-size:0.75rem;color:#374151;padding:0.25rem 0">No blocks or tasks yet.</div>` : ""}}
    </div>
  </div>`;
}}

document.addEventListener("click", e => {{
  const header = e.target.closest(".project-header");
  if (!header) return;
  const body = header.nextElementSibling;
  const open = body.style.display !== "none";
  body.style.display = open ? "none" : "block";
  header.querySelector("span:last-child").textContent = open ? "▾" : "▴";
}});

function renderProjects() {{
  const el = document.getElementById("projects-list");
  if (!fleetData.projects.length) {{
    el.innerHTML = '<div style="color:#475569;font-size:0.85rem;padding:1rem 0">No projects yet.</div>';
    return;
  }}
  el.innerHTML = fleetData.projects.map(projectCard).join("");
}}

function openAddProject() {{
  ["pj-name","pj-budget","pj-progress","pj-notes"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("pj-status").value = "Ongoing";
  document.getElementById("modal-project").classList.add("open");
}}

async function submitProject() {{
  const progressRaw = document.getElementById("pj-progress").value.trim() || "0/100";
  const [cur, total] = parseProgress(progressRaw);
  const record = {{
    name: document.getElementById("pj-name").value.trim(),
    budget: document.getElementById("pj-budget").value.trim(),
    status: document.getElementById("pj-status").value,
    progress: cur,
    progress_total: total,
    progress_str: progressRaw,
    started: new Date().toISOString().slice(0, 10),
    notes: document.getElementById("pj-notes").value.trim(),
  }};
  const res = await fetch(api("/api/fleet/projects"), {{
    method: "POST", headers: {{"Content-Type":"application/json"}}, body: JSON.stringify(record),
  }}).then(r => r.json());
  if (res.ok) {{
    fleetData.projects.push(res.record);
    renderProjects();
    closeModal("modal-project");
  }}
}}

// ── Templates ──────────────────────────────────────────────────────────────

let templates = [];

const TYPE_LABELS = {{
  ha_dashboard: "HA Dashboard",
  python_addon: "Python Addon",
  yaml_config: "YAML Config",
  code_project: "Code Project",
  custom: "Custom",
}};

function templateCard(t) {{
  return `
  <div class="tmpl-card">
    <div class="tmpl-name">${{t.name || "Untitled"}}</div>
    <div class="tmpl-desc">${{t.description || "No description."}}</div>
    <div class="tmpl-foot">
      <span class="tmpl-type">${{TYPE_LABELS[t.type] || t.type || "—"}}</span>
      ${{t.author ? `<span style="font-size:0.68rem;color:#374151">by ${{t.author}}</span>` : ""}}
      <button class="btn btn-ghost btn-sm" style="margin-left:auto"
        onclick="useTemplate('${{t.id}}')">Use</button>
      <button class="btn btn-ghost btn-sm" style="font-size:0.7rem;color:#374151"
        onclick="deleteTemplate('${{t.id}}')">✕</button>
    </div>
  </div>`;
}}

async function loadTemplates() {{
  const res = await fetch(api("/api/fleet/templates")).then(r => r.json());
  templates = res.templates || [];
  renderTemplates();
}}

function renderTemplates() {{
  const el = document.getElementById("tmpl-grid");
  if (!templates.length) {{
    el.innerHTML = '<div style="color:#475569;font-size:0.85rem;padding:1rem 0;grid-column:1/-1">No templates yet. Create one to define a reusable job type.</div>';
    return;
  }}
  el.innerHTML = templates.map(templateCard).join("");
}}

function openAddTemplate() {{
  ["tm-name","tm-desc","tm-author"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("tm-type").value = "ha_dashboard";
  document.getElementById("modal-template").classList.add("open");
}}

async function submitTemplate() {{
  const record = {{
    name: document.getElementById("tm-name").value.trim(),
    type: document.getElementById("tm-type").value,
    description: document.getElementById("tm-desc").value.trim(),
    author: document.getElementById("tm-author").value.trim(),
  }};
  const res = await fetch(api("/api/fleet/templates"), {{
    method: "POST", headers: {{"Content-Type":"application/json"}}, body: JSON.stringify(record),
  }}).then(r => r.json());
  if (res.ok) {{
    templates.push(res.record);
    renderTemplates();
    closeModal("modal-template");
  }}
}}

async function deleteTemplate(id) {{
  if (!confirm("Delete this template?")) return;
  const res = await fetch(api("/api/fleet/templates/" + id), {{ method: "DELETE" }}).then(r => r.json());
  if (res.ok) {{
    templates = templates.filter(t => t.id !== id);
    renderTemplates();
  }}
}}

function useTemplate(id) {{
  alert("Template job launch coming in the next build.");
}}

// ── Modal helpers ──────────────────────────────────────────────────────────

function closeModal(id) {{
  document.getElementById(id).classList.remove("open");
}}

document.querySelectorAll(".modal-overlay").forEach(el => {{
  el.addEventListener("click", e => {{ if (e.target === el) el.classList.remove("open"); }});
}});

load();
</script>
</body>
</html>"""
