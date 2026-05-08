from __future__ import annotations

import app.config as _config

_config.load()

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.snapshot import capabilities, status
from app.workers import configured_workers, test_worker
from app.harnesses import load_harnesses, save_user_harness
from app.roles import load_roles, save_roles, swap_roles, ROLE_ORDER, ROLE_LABELS, ROLE_META, ADVISOR_ROLE
from fastapi import BackgroundTasks
from app.jobs import create_job, load_job, list_jobs, read_stage_output, cancel_job, delete_job, restart_job
from app.pipeline import run_pipeline, run_stage
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


@app.put("/api/harnesses/{harness_id}")
async def api_harness_save(harness_id: str, payload: dict) -> dict:
    existing = load_harnesses().get(harness_id)
    if existing is None:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    updated = {**existing, **payload}
    save_user_harness(harness_id, updated)
    return {"ok": True, "harness": updated}


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


# ── Job API ──────────────────────────────────────────────────────────────────

@app.get("/api/jobs")
async def api_jobs_list() -> dict:
    return {"jobs": list_jobs()}


@app.post("/api/jobs")
async def api_job_create(payload: dict, background_tasks: BackgroundTasks) -> dict:
    job = create_job(payload)
    if payload.get("autorun", True):
        background_tasks.add_task(run_pipeline, job["id"])
    return {"ok": True, "job": job}


@app.get("/api/jobs/{job_id}")
async def api_job_get(job_id: str) -> dict:
    job = load_job(job_id)
    if not job:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return {"ok": True, "job": job}


@app.post("/api/jobs/{job_id}/run")
async def api_job_run(job_id: str, background_tasks: BackgroundTasks) -> dict:
    job = load_job(job_id)
    if not job:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    background_tasks.add_task(run_pipeline, job_id)
    return {"ok": True, "job_id": job_id}


@app.post("/api/jobs/{job_id}/run-stage/{stage}")
async def api_job_run_stage(job_id: str, stage: str) -> dict:
    job = load_job(job_id)
    if not job:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    result = await run_stage(job_id, stage)
    return result


@app.get("/api/jobs/{job_id}/stage/{stage}")
async def api_job_stage_output(job_id: str, stage: str) -> dict:
    output = read_stage_output(job_id, stage)
    if output is None:
        return JSONResponse({"ok": False, "error": "no output yet"}, status_code=404)
    return {"ok": True, "stage": stage, "output": output}


@app.post("/api/jobs/{job_id}/cancel")
async def api_job_cancel(job_id: str) -> dict:
    ok = cancel_job(job_id)
    return {"ok": ok}


@app.post("/api/jobs/{job_id}/restart")
async def api_job_restart(job_id: str, background_tasks: BackgroundTasks) -> dict:
    job = restart_job(job_id)
    if not job:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    background_tasks.add_task(run_pipeline, job_id)
    return {"ok": True, "job_id": job_id}


@app.delete("/api/jobs/{job_id}")
async def api_job_delete(job_id: str) -> dict:
    ok = delete_job(job_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return {"ok": True}


@app.patch("/api/jobs/{job_id}/pipeline")
async def api_job_reassign(job_id: str, payload: dict) -> dict:
    job = load_job(job_id)
    if not job:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if "pipeline" in payload:
        job["pipeline"] = payload["pipeline"]
    if "target_dashboard" in payload:
        job["target_dashboard"] = payload["target_dashboard"]
    from app.jobs import save_job
    save_job(job)
    return {"ok": True, "job": job}


@app.get("/api/pipeline-rules")
async def api_pipeline_rules_get() -> dict:
    from app.pipeline_rules import load_rules
    return {"rules": load_rules()}


@app.post("/api/pipeline-rules")
async def api_pipeline_rules_save(payload: dict) -> dict:
    from app.pipeline_rules import save_rules
    save_rules(payload)
    return {"ok": True}


@app.get("/api/fleet/templates")
async def api_templates_get() -> dict:
    builtin = [
        {
            "id": f"__role_{role}",
            "name": f"{meta['label']} — Instruction",
            "type": "instruction",
            "description": meta["description"],
            "author": "fleet-command",
            "body": meta["persona"],
            "_builtin": True,
        }
        for role, meta in ROLE_META.items()
    ]
    user = load_templates()
    return {"templates": builtin + user}


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
  <div class="tab" onclick="switchTab('pipeline', this); loadPipelineTab()">Pipeline</div>
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
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
    <div class="section-title" style="margin:0">Job Queue</div>
    <button class="btn btn-primary btn-sm" onclick="openNewJob()">+ New Job</button>
  </div>
  <div id="job-list"></div>
</div>

<!-- ── Harnesses tab ── -->
<div class="tab-panel" id="tab-harnesses">
  <div class="section-title">Registered Models</div>
  <div class="harness-grid" id="harness-grid"></div>
</div>

<!-- ── Pipeline tab ── -->
<div class="tab-panel" id="tab-pipeline">
  <div class="section-title">Pipeline Visualizer</div>
  <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:1rem">
    <label style="font-size:0.82rem;color:#94a3b8">Job</label>
    <select id="pl-job-select" style="font-size:0.82rem;background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:0.25rem 0.5rem" onchange="renderPipelineNodes()"></select>
    <button class="btn btn-ghost btn-sm" onclick="loadPipelineTab()">↺ Refresh</button>
  </div>
  <div id="pl-canvas" style="overflow-x:auto;padding:1rem 0"></div>
</div>

<!-- ── Templates tab ── -->
<div class="tab-panel" id="tab-templates">
  <div class="section-title">Worker Configs — What Each AI Is Running</div>
  <div class="tmpl-grid" id="tmpl-grid"></div>
  <div style="margin-top:1.5rem;display:flex;justify-content:space-between;align-items:center">
    <div class="section-title" style="margin:0">Saved Job Templates</div>
    <button class="btn btn-ghost btn-sm" onclick="openAddTemplate()">+ New Template</button>
  </div>
  <div class="tmpl-grid" id="job-tmpl-grid" style="margin-top:0.75rem"></div>
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

<div class="modal-overlay" id="modal-new-job">
  <div class="modal" style="width:min(560px,94vw)">
    <h3>New Job</h3>
    <div class="field">
      <label>Output Type</label>
      <select id="nj-type">
        <option value="ha_dashboard">HA Dashboard</option>
        <option value="yaml_config">YAML Config</option>
        <option value="python_code">Python Code</option>
      </select>
    </div>
    <div class="field" style="margin-top:0.5rem">
      <label>Job Specification — describe what to build</label>
      <textarea id="nj-spec" rows="5" style="resize:vertical;font-family:monospace;font-size:0.82rem"
        placeholder="e.g. Build a dashboard with a weather card, an energy card, and a button to toggle the living room light."></textarea>
    </div>
    <div class="field" style="margin-top:0.5rem">
      <label>Target Dashboard Slug (HA)</label>
      <input id="nj-target" placeholder="fleet_output" value="fleet_output">
    </div>
    <div class="field" style="margin-top:0.5rem">
      <label>Pipeline Stages</label>
      <div style="display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.3rem;font-size:0.82rem">
        <label><input type="checkbox" id="nj-pm"> PM</label>
        <label><input type="checkbox" id="nj-mgr"> Manager</label>
        <label><input type="checkbox" id="nj-gen" checked> Generator</label>
        <label><input type="checkbox" id="nj-rev"> Reviewer</label>
        <label><input type="checkbox" id="nj-sup"> Supervisor</label>
      </div>
      <div style="margin-top:0.4rem;display:flex;gap:0.5rem">
        <button class="btn btn-ghost btn-sm" style="font-size:0.72rem;padding:0.15rem 0.5rem" onclick="setChain('full')">Full chain</button>
        <button class="btn btn-ghost btn-sm" style="font-size:0.72rem;padding:0.15rem 0.5rem" onclick="setChain('gen')">Generator only</button>
        <button class="btn btn-ghost btn-sm" style="font-size:0.72rem;padding:0.15rem 0.5rem" onclick="setChain('genrev')">Gen + Review</button>
      </div>
    </div>
    <div class="modal-actions" style="margin-top:0.75rem">
      <button class="btn btn-ghost btn-sm" onclick="closeModal('modal-new-job')">Cancel</button>
      <button class="btn btn-ghost btn-sm" onclick="submitJob(false)">Create (no autorun)</button>
      <button class="btn btn-primary btn-sm" onclick="submitJob(true)">Run Now</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="modal-harness">
  <div class="modal" style="width:min(520px,92vw)">
    <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.25rem">
      <h3 id="mh-title" style="flex:1"></h3>
      <span id="mh-cost"></span>
    </div>
    <div id="mh-caps" style="display:flex;gap:0.3rem;flex-wrap:wrap;margin-bottom:0.25rem"></div>
    <div id="mh-notes" style="font-size:0.72rem;color:#475569;font-style:italic;margin-bottom:0.5rem"></div>
    <div class="params-grid">
      <div class="field">
        <label>Request Format</label>
        <select id="mh-fmt">
          <option value="ollama_chat">ollama_chat</option>
          <option value="ollama_generate">ollama_generate</option>
          <option value="openai_responses">openai_responses</option>
          <option value="openai_chat">openai_chat</option>
          <option value="anthropic_messages">anthropic_messages</option>
          <option value="raw_prompt_json">raw_prompt_json</option>
        </select>
      </div>
      <div class="field">
        <label>Auth Type</label>
        <select id="mh-auth">
          <option value="none">none</option>
          <option value="bearer">bearer</option>
          <option value="x_api_key">x_api_key</option>
          <option value="custom_header">custom_header</option>
        </select>
      </div>
      <div class="field">
        <label>Temperature</label>
        <input type="number" id="mh-temp" min="0" max="2" step="0.1">
      </div>
      <div class="field">
        <label>Concurrency</label>
        <input type="number" id="mh-conc" min="1" step="1">
      </div>
    </div>
    <div class="field" style="margin-top:0.5rem">
      <label>Endpoint</label>
      <input type="text" id="mh-ep">
    </div>
    <div class="field" style="margin-top:0.5rem">
      <label>API Path</label>
      <input type="text" id="mh-path">
    </div>
    <div class="modal-actions" style="margin-top:0.75rem">
      <button class="btn btn-ghost btn-sm" onclick="closeModal('modal-harness')">Close</button>
      <button class="btn btn-primary btn-sm" onclick="saveHarnessFromModal()">Save</button>
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
  configuredWorkers = wRes.workers || [];
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
      <span style="cursor:pointer" onclick="if('${{hid}}') openHarnessDetail('${{hid}}')" title="View model config">${{costBadge(h)}}</span>
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
    [...roleOrder, advisorRole].map(r => roles[r]?.harness_id).filter(Boolean)
  );
  const unassigned = Object.entries(harnesses).filter(([id]) => !assignedIds.has(id));
  if (unassigned.length === 0) {{
    el.innerHTML = '<span style="font-size:0.78rem;color:#374151">All models assigned to roles.</span>';
    return;
  }}
  el.innerHTML = unassigned.map(([id, h]) => `
    <div class="pool-chip" style="cursor:pointer" onclick="openHarnessDetail('${{id}}')" title="View / edit config">
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
  renderRoster();
  renderPool();
  renderStaff();
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
  if (name === "jobs") loadJobs();
  if (name !== "jobs") stopLivePoll();
}}

const REQUEST_FORMATS = ["ollama_chat","ollama_generate","openai_responses","openai_chat","anthropic_messages","raw_prompt_json"];
const AUTH_TYPES = ["none","bearer","x_api_key","custom_header"];

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
    const fmtOpts = REQUEST_FORMATS.map(f => `<option value="${{f}}" ${{f === h.request_format ? "selected" : ""}}>${{f}}</option>`).join("");
    const authOpts = AUTH_TYPES.map(a => `<option value="${{a}}" ${{a === h.auth_type ? "selected" : ""}}>${{a}}</option>`).join("");
    return `
    <div class="harness-card" style="flex-direction:column;gap:0">
      <div style="display:flex;align-items:flex-start;gap:1rem">
        <div class="harness-info" style="flex:1">
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
        <button class="btn btn-ghost btn-sm" onclick="toggleHarnessEdit('${{id}}')" title="Edit">⚙</button>
      </div>
      <div id="hedit-${{id}}" style="display:none;margin-top:0.75rem;border-top:1px solid #1e293b;padding-top:0.75rem">
        <div class="params-grid" style="margin-bottom:0.6rem">
          <div class="param-row">
            <label>Request Format</label>
            <select id="hf-fmt-${{id}}" style="background:#0f1117;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.82rem;padding:0.25rem 0.4rem;width:100%">${{fmtOpts}}</select>
          </div>
          <div class="param-row">
            <label>Auth Type</label>
            <select id="hf-auth-${{id}}" style="background:#0f1117;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.82rem;padding:0.25rem 0.4rem;width:100%">${{authOpts}}</select>
          </div>
          <div class="param-row">
            <label>Temperature</label>
            <input type="number" id="hf-temp-${{id}}" min="0" max="2" step="0.1" value="${{h.params?.temperature ?? 0}}"
              style="background:#0f1117;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.82rem;padding:0.25rem 0.4rem;width:100%">
          </div>
          <div class="param-row">
            <label>Concurrency</label>
            <input type="number" id="hf-conc-${{id}}" min="1" step="1" value="${{h.concurrency ?? 1}}"
              style="background:#0f1117;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.82rem;padding:0.25rem 0.4rem;width:100%">
          </div>
        </div>
        <div class="param-row" style="margin-bottom:0.5rem">
          <label>Endpoint</label>
          <input type="text" id="hf-ep-${{id}}" value="${{h.endpoint || ""}}"
            style="background:#0f1117;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.82rem;padding:0.25rem 0.4rem;width:100%">
        </div>
        <div class="param-row" style="margin-bottom:0.6rem">
          <label>API Path</label>
          <input type="text" id="hf-path-${{id}}" value="${{h.api_path || ""}}"
            style="background:#0f1117;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.82rem;padding:0.25rem 0.4rem;width:100%">
        </div>
        <div style="display:flex;justify-content:flex-end;gap:0.5rem">
          <button class="btn btn-ghost btn-sm" onclick="toggleHarnessEdit('${{id}}')">Cancel</button>
          <button class="btn btn-primary btn-sm" onclick="saveHarness('${{id}}')">Save</button>
        </div>
      </div>
    </div>`;
  }}).join("");
}}

function toggleHarnessEdit(id) {{
  const el = document.getElementById("hedit-" + id);
  el.style.display = el.style.display === "none" ? "block" : "none";
}}

async function saveHarness(id) {{
  const h = harnesses[id];
  const temp = parseFloat(document.getElementById("hf-temp-" + id).value);
  const payload = {{
    request_format: document.getElementById("hf-fmt-" + id).value,
    auth_type: document.getElementById("hf-auth-" + id).value,
    endpoint: document.getElementById("hf-ep-" + id).value.trim(),
    api_path: document.getElementById("hf-path-" + id).value.trim(),
    concurrency: parseInt(document.getElementById("hf-conc-" + id).value) || 1,
    params: {{ ...h.params, temperature: isNaN(temp) ? 0 : temp }},
  }};
  const res = await fetch(api("/api/harnesses/" + id), {{
    method: "PUT", headers: {{"Content-Type":"application/json"}}, body: JSON.stringify(payload),
  }}).then(r => r.json());
  if (res.ok) {{
    harnesses[id] = res.harness;
    renderHarnesses();
    renderStaff();
  }}
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
  const caps = (s.capabilities || []);
  const capsHtml = caps.length
    ? `<div style="display:flex;gap:0.3rem;flex-wrap:wrap;margin-top:0.3rem">${{caps.map(c => `<span class="cap-tag">${{c}}</span>`).join("")}}</div>`
    : "";

  const allRoles = [...roleOrder, advisorRole];
  const currentRole = s._harness_id
    ? allRoles.find(r => roles[r]?.harness_id === s._harness_id) || ""
    : "";
  const roleOpts = `<option value="">— no role —</option>` +
    allRoles.map(r => `<option value="${{r}}" ${{r === currentRole ? "selected" : ""}}>${{roleMeta[r]?.label || r}}</option>`).join("");

  const assignId = s._harness_id || s._worker_id;
  const roleAssign = assignId ? `
    <div style="display:flex;align-items:center;gap:0.4rem;margin-top:0.5rem">
      <select style="flex:1;background:#0f1117;border:1px solid #334155;border-radius:5px;color:#e2e8f0;font-size:0.78rem;padding:0.25rem 0.4rem"
        id="role-assign-${{assignId}}" data-harness-id="${{s._harness_id || ""}}">${{roleOpts}}</select>
      <button class="btn btn-ghost btn-sm" onclick="assignWorkerRole('${{assignId}}')">Assign</button>
    </div>` : "";

  return `
  <div class="staff-card ${{type}}" data-id="${{s.id}}">
    <div class="staff-avatar ${{type}}">${{icon}}</div>
    <div class="staff-body" style="flex:1">
      <div style="display:flex;align-items:center;gap:0.5rem">
        <span class="staff-name">${{s.name || "Unnamed"}}</span>
        <span class="staff-status ${{statusCls}}">${{s.status || "—"}}</span>
      </div>
      <div class="staff-role">${{s.role || "—"}}</div>
      <div class="staff-meta">
        ${{s.score ? `<span>${{s.score}}</span>` : ""}}
        ${{s.budget ? `<span>${{s.budget}}</span>` : ""}}
        ${{s.hired ? `<span>📅 ${{s.hired}}</span>` : ""}}
      </div>
      ${{s.profile ? `<div style="font-size:0.72rem;color:#334155;margin-top:0.2rem;font-style:italic">${{s.profile}}</div>` : ""}}
      ${{capsHtml}}
      ${{taskHtml}}
      ${{roleAssign}}
    </div>
    ${{!s._readonly ? `<button class="btn btn-ghost btn-sm" onclick="deleteStaff(${{s.id}})" title="Remove" style="font-size:0.7rem;color:#374151">✕</button>` : ""}}
  </div>`;
}}

function workerToStaff(w) {{
  const statusMap = {{
    "Configured": "Available",
    "Disabled": "On Vacation",
    "Missing URL": "Unavailable",
    "Missing API key": "Unavailable",
  }};
  const matchedHarness = Object.values(harnesses).find(h => h.model === w.model) || null;
  const caps = matchedHarness?.capabilities || [];
  return {{
    id: "w" + w.id,
    type: "AR",
    name: w.name || ("Worker " + w.id),
    role: w.role || "worker",
    budget: w.provider || "—",
    status: statusMap[w.status] || w.status,
    score: "",
    profile: [w.model, w.request_format].filter(Boolean).join(" · "),
    assigned_work: [],
    capabilities: caps,
    _worker_id: w.id,
    _harness_match: matchedHarness,
    _readonly: true,
  }};
}}

function harnessToStaff(harnessId, h) {{
  const matchedWorker = configuredWorkers.find(w => w.model === h.model);
  const statusMap = {{ "Configured": "Available", "Disabled": "On Vacation", "Missing URL": "Unavailable", "Missing API key": "Unavailable" }};
  const costLabel = h.cost_type === "local" ? "local" : h.cost_type === "cloud_metered" ? "metered" : h.cost_type === "cloud_shared" ? "cloud shared" : h.cost_type || "—";
  return {{
    id: "h_" + harnessId,
    type: "AR",
    name: h.display_name || h.model || harnessId,
    role: costLabel,
    budget: matchedWorker ? ("Slot " + matchedWorker.id + " — " + matchedWorker.name) : "unslotted",
    status: matchedWorker ? (statusMap[matchedWorker.status] || matchedWorker.status) : "Available",
    score: h.context_window ? ((h.context_window/1000).toFixed(0) + "k ctx") : "",
    profile: h.notes || "",
    capabilities: h.capabilities || [],
    _harness_id: harnessId,
    _harness_match: h,
    _worker_id: matchedWorker ? matchedWorker.id : null,
    _readonly: true,
  }};
}}

function renderStaff() {{
  const hr = fleetData.staff.filter(s => s.type === "HR");
  const arManual = fleetData.staff.filter(s => s.type === "AR");
  const arHarnesses = Object.entries(harnesses).map(([id, h]) => harnessToStaff(id, h));
  const ar = [...arHarnesses, ...arManual];

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

async function assignWorkerRole(staffId) {{
  const sel = document.getElementById("role-assign-" + staffId);
  const role = sel?.value;
  if (!role) return;
  const harnessId = sel.dataset.harnessId;
  roles[role] = {{ harness_id: harnessId || null, params: {{}} }};
  await fetch(api("/api/roles"), {{
    method: "POST", headers: {{"Content-Type":"application/json"}},
    body: JSON.stringify({{ assignments: roles }}),
  }});
  renderRoster();
  renderPool();
  renderStaff();
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

// ── Jobs ───────────────────────────────────────────────────────────────────

let _jobPollTimer = null;
let _jobLivePollTimer = null;
let _openJobId = null;

const STATUS_COLORS = {{
  pending:   "#475569",
  running:   "#fbbf24",
  reviewing: "#818cf8",
  done:      "#4ade80",
  failed:    "#f87171",
}};

function jobStatusBadge(status) {{
  const color = STATUS_COLORS[status] || "#475569";
  return `<span style="font-size:0.68rem;padding:0.15rem 0.5rem;border-radius:3px;font-weight:500;background:#0f1117;color:${{color}};border:1px solid ${{color}}">${{status}}</span>`;
}}

function jobCard(j) {{
  const stages = j.stages || {{}};
  const stageHtml = Object.entries(stages).map(([name, s]) => {{
    const color = s.status === "done" ? "#4ade80" : s.status === "error" ? "#f87171" : "#fbbf24";
    const isRejected = s.status === "done" && s.preview && s.preview.trim().toUpperCase().startsWith("REJECTED");
    const badge = isRejected ? "#f87171" : color;
    const label = isRejected ? name + ": rejected" : name + ": " + s.status;
    return `<span style="font-size:0.7rem;color:${{badge}};cursor:pointer;text-decoration:underline dotted"
      onclick="toggleStageOutput('${{j.id}}','${{name}}')">${{label}}</span>`;
  }}).join(" · ");

  const stageOutputPanels = Object.keys(stages).map(name =>
    `<div id="so-${{j.id}}-${{name}}" style="display:none;margin-bottom:0.5rem">
      <div style="font-size:0.65rem;color:#475569;margin-bottom:0.15rem;text-transform:uppercase;letter-spacing:0.05em">${{name}} output</div>
      <pre id="so-pre-${{j.id}}-${{name}}" style="font-size:0.7rem;color:#94a3b8;background:#0a0d14;border:1px solid #1e293b;border-radius:5px;padding:0.5rem;max-height:200px;overflow:auto;white-space:pre-wrap;margin:0">Loading…</pre>
    </div>`
  ).join("");

  const logHtml = (j.log || []).map(l =>
    `<div><span style="color:#334155">${{l.ts?.slice(11,19) || ""}}</span> <span style="color:#475569">[${{l.stage}}]</span> <span style="color:#94a3b8">${{l.msg}}</span></div>`
  ).join("") || '<span style="color:#334155">No log yet.</span>';

  const hasFinal = j.status === "done" && j.final_output;
  const isActive = j.status === "running" || j.status === "reviewing";

  return `
  <div style="background:#1e2330;border:1px solid ${{isActive ? "#fbbf24" : "#2d3748"}};border-radius:10px;margin-bottom:0.75rem;overflow:hidden;transition:border-color 0.3s">
    <div style="padding:0.75rem 1rem;display:flex;align-items:center;gap:0.75rem;cursor:pointer"
      onclick="toggleJobDetail('${{j.id}}')">
      <div style="flex:1">
        <div style="font-size:0.88rem;font-weight:600;color:#e2e8f0">#${{j.id}} — ${{j.type}}</div>
        <div style="font-size:0.72rem;color:#475569;margin-top:0.1rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:55vw">${{j.spec?.slice(0,120) || "—"}}</div>
      </div>
      <span id="jstatus-${{j.id}}">${{jobStatusBadge(j.status)}}</span>
      <span style="color:#475569;font-size:0.8rem">▾</span>
    </div>
    <div id="jdetail-${{j.id}}" style="display:none;padding:0 1rem 0.75rem;border-top:1px solid #1e293b">
      <div id="jstages-${{j.id}}" style="font-size:0.72rem;margin-bottom:0.5rem;min-height:1rem;display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center">
        ${{stageHtml || '<span style="color:#475569">No stages run yet</span>'}}
        <span style="font-size:0.62rem;color:#334155">↑ click stage to inspect</span>
      </div>
      ${{stageOutputPanels}}
      <div style="font-size:0.68rem;color:#475569;margin-bottom:0.2rem">Log ${{isActive ? '— live' : ''}}</div>
      <div id="jlog-${{j.id}}" style="font-family:monospace;font-size:0.72rem;background:#0f1117;border-radius:5px;padding:0.5rem;height:140px;overflow-y:auto;margin-bottom:0.6rem;line-height:1.6">${{logHtml}}</div>
      ${{hasFinal ? `<div style="margin-bottom:0.6rem"><div style="font-size:0.68rem;color:#475569;margin-bottom:0.2rem">Final Output</div><pre style="font-size:0.7rem;color:#94a3b8;background:#0f1117;padding:0.5rem;border-radius:5px;max-height:220px;overflow:auto;white-space:pre-wrap">${{j.final_output}}</pre></div>` : ""}}
      <div style="display:flex;gap:0.4rem;flex-wrap:wrap;align-items:center;margin-bottom:0.4rem">
        ${{j.status === "pending" ? `<button class="btn btn-primary btn-sm" onclick="runJob('${{j.id}}')">▶ Run</button>` : ""}}
        ${{isActive ? `<span style="font-size:0.75rem;color:#fbbf24">● Running…</span>` : ""}}
        ${{isActive ? `<button class="btn btn-ghost btn-sm" onclick="cancelJob('${{j.id}}')">⏸ Cancel</button>` : ""}}
        ${{(j.status === "failed" || j.status === "done" || j.status === "cancelled") ? `<button class="btn btn-ghost btn-sm" style="color:#fbbf24" onclick="restartJob('${{j.id}}')">↺ Retry</button>` : ""}}
        <button class="btn btn-ghost btn-sm" onclick="runJobStage('${{j.id}}','generator')">gen only</button>
        <button class="btn btn-ghost btn-sm" onclick="runJobStage('${{j.id}}','reviewer')">review only</button>
        <button class="btn btn-ghost btn-sm" style="margin-left:auto;color:#f87171" onclick="removeJob('${{j.id}}')">✕ Remove</button>
      </div>
      <div style="display:flex;gap:0.4rem;align-items:center;flex-wrap:wrap">
        <span style="font-size:0.7rem;color:#475569">Reassign:</span>
        <select id="reassign-${{j.id}}" style="background:#0f1117;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.75rem;padding:0.2rem 0.35rem">
          <option value="generator">generator only</option>
          <option value="generator,reviewer">gen + reviewer</option>
          <option value="generator,reviewer,supervisor">full chain</option>
        </select>
        <button class="btn btn-ghost btn-sm" onclick="reassignJob('${{j.id}}')">Apply</button>
      </div>
    </div>
  </div>`;
}}

async function loadJobs() {{
  const res = await fetch(api("/api/jobs")).then(r => r.json());
  const jobs = res.jobs || [];
  const el = document.getElementById("job-list");
  if (!jobs.length) {{
    el.innerHTML = '<div style="color:#475569;font-size:0.85rem;padding:1rem 0">No jobs yet. Click + New Job to start.</div>';
    return;
  }}
  el.innerHTML = jobs.map(jobCard).join("");

  // Re-open previously open panel without resetting live poll
  if (_openJobId) {{
    const panel = document.getElementById("jdetail-" + _openJobId);
    if (panel) panel.style.display = "block";
  }}
}}

function toggleJobDetail(id) {{
  const el = document.getElementById("jdetail-" + id);
  if (!el) return;
  const open = el.style.display !== "none";
  el.style.display = open ? "none" : "block";
  if (!open) {{
    _openJobId = id;
    startLivePoll(id);
  }} else {{
    _openJobId = null;
    stopLivePoll();
  }}
}}

function startLivePoll(id) {{
  stopLivePoll();
  _jobLivePollTimer = setInterval(() => refreshJobDetail(id), 2000);
}}

function stopLivePoll() {{
  if (_jobLivePollTimer) {{ clearInterval(_jobLivePollTimer); _jobLivePollTimer = null; }}
}}

async function refreshJobDetail(id) {{
  const res = await fetch(api("/api/jobs/" + id)).then(r => r.json());
  if (!res.ok) return;
  const j = res.job;

  const logEl = document.getElementById("jlog-" + id);
  const stageEl = document.getElementById("jstages-" + id);
  const statusEl = document.getElementById("jstatus-" + id);

  if (logEl) {{
    logEl.innerHTML = (j.log || []).map(l =>
      `<div><span style="color:#334155">${{l.ts?.slice(11,19) || ""}}</span> <span style="color:#475569">[${{l.stage}}]</span> <span style="color:#94a3b8">${{l.msg}}</span></div>`
    ).join("") || '<span style="color:#334155">No log yet.</span>';
    logEl.scrollTop = logEl.scrollHeight;
  }}

  if (stageEl) {{
    const stages = j.stages || {{}};
    stageEl.innerHTML = Object.entries(stages).map(([name, s]) =>
      `<span style="font-size:0.7rem;color:${{s.status === "done" ? "#4ade80" : s.status === "error" ? "#f87171" : "#fbbf24"}}">${{name}}: ${{s.status}}</span>`
    ).join(" · ");
  }}

  if (statusEl) {{
    statusEl.innerHTML = jobStatusBadge(j.status);
  }}

  if (j.status === "done" || j.status === "failed" || j.status === "cancelled") {{
    stopLivePoll();
    loadJobs();
  }}
}}

async function runJob(id) {{
  await fetch(api("/api/jobs/" + id + "/run"), {{ method: "POST" }});
  await loadJobs();
  if (_jobPollTimer) clearTimeout(_jobPollTimer);
  _jobPollTimer = setTimeout(() => {{ _jobPollTimer = null; loadJobs(); }}, 3000);
}}

async function runJobStage(id, stage) {{
  const res = await fetch(api("/api/jobs/" + id + "/run-stage/" + stage), {{ method: "POST" }}).then(r => r.json());
  await loadJobs();
  alert(res.ok ? `Stage '${{stage}}' done.` : `Error: ${{res.error}}`);
}}

async function toggleStageOutput(jobId, stage) {{
  const panel = document.getElementById("so-" + jobId + "-" + stage);
  const pre = document.getElementById("so-pre-" + jobId + "-" + stage);
  if (!panel) return;
  const isOpen = panel.style.display !== "none";
  panel.style.display = isOpen ? "none" : "block";
  if (!isOpen && pre.textContent === "Loading…") {{
    const res = await fetch(api("/api/jobs/" + jobId + "/stage/" + stage)).then(r => r.json());
    pre.textContent = res.ok ? (res.output || "(empty)") : "No output for this stage yet.";
    const isRejected = pre.textContent.trim().toUpperCase().startsWith("REJECTED");
    pre.style.color = isRejected ? "#f87171" : "#94a3b8";
  }}
}}

function openNewJob() {{
  document.getElementById("nj-spec").value = "";
  document.getElementById("nj-target").value = "fleet_output";
  document.getElementById("modal-new-job").classList.add("open");
}}

function setChain(preset) {{
  const ids = ["nj-pm","nj-mgr","nj-gen","nj-rev","nj-sup"];
  const vals = preset === "full"   ? [true,true,true,true,true]
             : preset === "genrev" ? [false,false,true,true,false]
             :                       [false,false,true,false,false];
  ids.forEach((id,i) => {{ document.getElementById(id).checked = vals[i]; }});
}}

async function submitJob(autorun) {{
  const pipeline = [];
  if (document.getElementById("nj-pm").checked)  pipeline.push("project_manager");
  if (document.getElementById("nj-mgr").checked) pipeline.push("manager");
  if (document.getElementById("nj-gen").checked) pipeline.push("generator");
  if (document.getElementById("nj-rev").checked) pipeline.push("reviewer");
  if (document.getElementById("nj-sup").checked) pipeline.push("supervisor");
  if (!pipeline.length) {{ alert("Select at least one pipeline stage."); return; }}

  const payload = {{
    type: document.getElementById("nj-type").value,
    spec: document.getElementById("nj-spec").value.trim(),
    target_dashboard: document.getElementById("nj-target").value.trim() || "fleet_output",
    pipeline,
    autorun,
  }};
  if (!payload.spec) {{ alert("Job specification required."); return; }}

  const res = await fetch(api("/api/jobs"), {{
    method: "POST", headers: {{"Content-Type":"application/json"}}, body: JSON.stringify(payload),
  }}).then(r => r.json());

  if (res.ok) {{
    closeModal("modal-new-job");
    switchTab("jobs", document.querySelector(".tab:nth-child(3)"));
    await loadJobs();
  }}
}}

// ── Pipeline tab ─────────────────────────────────────────────────────────────

let _plJobs = [];
let _plPollTimer = null;

async function loadPipelineTab() {{
  const res = await fetch(api("/api/jobs")).then(r => r.json());
  _plJobs = res.jobs || [];
  const sel = document.getElementById("pl-job-select");
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = _plJobs.length
    ? _plJobs.map(j => `<option value="${{j.id}}"${{j.id===prev?" selected":""}}>${{j.id}} — ${{j.status}} (${{(j.spec||"").slice(0,40)}})</option>`).join("")
    : `<option>No jobs yet</option>`;
  renderPipelineNodes();
  _plSchedulePoll();
}}

function _plSchedulePoll() {{
  clearTimeout(_plPollTimer);
  const sel = document.getElementById("pl-job-select");
  if (!sel) return;
  const job = _plJobs.find(j => j.id === sel.value);
  if (job && (job.status === "running" || job.status === "pending")) {{
    _plPollTimer = setTimeout(loadPipelineTab, 2000);
  }}
}}

function renderPipelineNodes() {{
  const sel = document.getElementById("pl-job-select");
  const canvas = document.getElementById("pl-canvas");
  if (!sel || !canvas) return;
  const job = _plJobs.find(j => j.id === sel.value);
  if (!job) {{ canvas.innerHTML = `<p style="color:#64748b">No job selected.</p>`; return; }}

  const pipeline = job.pipeline || ["generator"];
  const stages = job.stages || {{}};
  const STATUS_COLOR = {{
    running: "#f59e0b", done: "#22c55e", error: "#ef4444",
    pending: "#475569", reviewing: "#818cf8",
  }};
  const LABEL = {{
    project_manager: "PM", manager: "Manager", generator: "Generator",
    reviewer: "Reviewer", supervisor: "Supervisor", advisor: "Advisor",
  }};
  const EDGE_LABEL = {{
    manager: "plan", generator: "brief", reviewer: "YAML", supervisor: "reviewed", advisor: "escalation",
  }};

  const nodeW = 140, nodeH = 96, gapX = 80, padY = 20;
  const totalW = pipeline.length * nodeW + (pipeline.length - 1) * gapX + 40;
  const totalH = nodeH + padY * 2 + 30;

  let html = `<svg width="${{totalW}}" height="${{totalH}}" style="display:block;min-width:100%">`;

  pipeline.forEach((stage, i) => {{
    if (i === 0) return;
    const x1 = 20 + (i - 1) * (nodeW + gapX) + nodeW;
    const x2 = 20 + i * (nodeW + gapX);
    const y = padY + nodeH / 2;
    const cx = (x1 + x2) / 2;
    const lbl = EDGE_LABEL[stage] || "";
    html += `<path d="M${{x1}},${{y}} C${{cx}},${{y}} ${{cx}},${{y}} ${{x2}},${{y}}" stroke="#334155" stroke-width="2" fill="none"/>`;
    html += `<text x="${{cx}}" y="${{y - 6}}" text-anchor="middle" font-size="9" fill="#64748b">${{lbl}}</text>`;
    html += `<circle cx="${{x1}}" cy="${{y}}" r="4" fill="#334155"/>`;
    html += `<circle cx="${{x2}}" cy="${{y}}" r="4" fill="#334155"/>`;
  }});

  pipeline.forEach((stage, i) => {{
    const x = 20 + i * (nodeW + gapX);
    const y = padY;
    const s = stages[stage] || {{}};
    const color = STATUS_COLOR[s.status] || STATUS_COLOR.pending;
    const label = LABEL[stage] || stage;
    const model = (s.handled_by && s.handled_by !== stage) ? `↑ ${{s.handled_by}}` : (s.model || "");
    const chars = s.preview ? s.preview.length : 0;
    const statusTxt = s.status || "pending";
    const hasOutput = s.status === "done" || s.status === "error";

    html += `
      <g class="pl-node" style="cursor:${{hasOutput?"pointer":"default"}}" onclick="${{hasOutput?`showNodeOutput('${{job.id}}','${{stage}}')`:""}}" >
        <rect x="${{x}}" y="${{y}}" width="${{nodeW}}" height="${{nodeH}}" rx="8"
              fill="#1e293b" stroke="${{color}}" stroke-width="2"/>
        <rect x="${{x}}" y="${{y}}" width="${{nodeW}}" height="24" rx="8" fill="${{color}}22"/>
        <rect x="${{x}}" y="${{y+16}}" width="${{nodeW}}" height="8" fill="${{color}}22"/>
        <text x="${{x+nodeW/2}}" y="${{y+16}}" text-anchor="middle" font-size="11" font-weight="bold" fill="${{color}}">${{label}}</text>
        <text x="${{x+nodeW/2}}" y="${{y+34}}" text-anchor="middle" font-size="9" fill="#94a3b8">${{statusTxt}}</text>
        <text x="${{x+nodeW/2}}" y="${{y+48}}" text-anchor="middle" font-size="8" fill="#64748b">${{model}}</text>
        <text x="${{x+nodeW/2}}" y="${{y+62}}" text-anchor="middle" font-size="8" fill="#475569">${{chars?chars+" chars":""}}</text>
        ${{hasOutput ? `<text x="${{x+nodeW/2}}" y="${{y+78}}" text-anchor="middle" font-size="8" fill="${{color}}">▶ view output</text>` : ""}}
      </g>`;
  }});

  html += `</svg>`;

  // Output panel below canvas
  html += `<div id="pl-output-panel" style="margin-top:1rem;display:none">
    <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem">
      <span id="pl-output-title" style="font-size:0.82rem;font-weight:600;color:#e2e8f0"></span>
      <button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick="document.getElementById('pl-output-panel').style.display='none'">✕</button>
    </div>
    <pre id="pl-output-code" style="background:#0f172a;color:#94a3b8;padding:1rem;border-radius:8px;font-size:0.75rem;overflow:auto;max-height:400px;white-space:pre-wrap;border:1px solid #334155"></pre>
  </div>`;

  canvas.innerHTML = html;
}}

async function showNodeOutput(jobId, stage) {{
  const res = await fetch(api(`/api/jobs/${{jobId}}/stage/${{stage}}`)).then(r => r.json());
  const panel = document.getElementById("pl-output-panel");
  const title = document.getElementById("pl-output-title");
  const code = document.getElementById("pl-output-code");
  if (!panel || !title || !code) return;
  title.textContent = stage + " output";
  const txt = res.output || res.error || "No output";
  const isRejected = txt.trim().toUpperCase().startsWith("REJECTED");
  code.textContent = txt;
  code.style.color = isRejected ? "#f87171" : "#94a3b8";
  panel.style.display = "block";
  panel.scrollIntoView({{behavior:"smooth", block:"nearest"}});
}}

async function cancelJob(id) {{
  await fetch(api("/api/jobs/" + id + "/cancel"), {{ method: "POST" }});
  stopLivePoll();
  _openJobId = null;
  loadJobs();
}}

async function restartJob(id) {{
  await fetch(api("/api/jobs/" + id + "/restart"), {{ method: "POST" }});
  _openJobId = id;
  await loadJobs();
  startLivePoll(id);
}}

async function removeJob(id) {{
  if (!confirm("Remove this job and all its output?")) return;
  await fetch(api("/api/jobs/" + id), {{ method: "DELETE" }});
  if (_openJobId === id) {{ _openJobId = null; stopLivePoll(); }}
  loadJobs();
}}

async function reassignJob(id) {{
  const sel = document.getElementById("reassign-" + id);
  const pipeline = sel.value.split(",");
  await fetch(api("/api/jobs/" + id + "/pipeline"), {{
    method: "PATCH", headers: {{"Content-Type":"application/json"}},
    body: JSON.stringify({{ pipeline }}),
  }});
  loadJobs();
}}

function refreshJobs() {{ loadJobs(); }}

// ── Harness detail modal (from pool chips + harness tab) ───────────────────

let _activeHarnessId = null;

function openHarnessDetail(id) {{
  const h = harnesses[id];
  if (!h) return;
  _activeHarnessId = id;
  document.getElementById("mh-title").textContent = h.display_name || id;
  document.getElementById("mh-notes").textContent = h.notes || "";
  document.getElementById("mh-caps").innerHTML = (h.capabilities || []).map(c => `<span class="cap-tag">${{c}}</span>`).join(" ");
  document.getElementById("mh-cost").innerHTML = costBadge(h);
  document.getElementById("mh-fmt").value = h.request_format || "ollama_chat";
  document.getElementById("mh-auth").value = h.auth_type || "none";
  document.getElementById("mh-temp").value = h.params?.temperature ?? 0;
  document.getElementById("mh-conc").value = h.concurrency ?? 1;
  document.getElementById("mh-ep").value = h.endpoint || "";
  document.getElementById("mh-path").value = h.api_path || "";
  document.getElementById("modal-harness").classList.add("open");
}}

async function saveHarnessFromModal() {{
  const id = _activeHarnessId;
  if (!id) return;
  const h = harnesses[id];
  const temp = parseFloat(document.getElementById("mh-temp").value);
  const payload = {{
    request_format: document.getElementById("mh-fmt").value,
    auth_type: document.getElementById("mh-auth").value,
    endpoint: document.getElementById("mh-ep").value.trim(),
    api_path: document.getElementById("mh-path").value.trim(),
    concurrency: parseInt(document.getElementById("mh-conc").value) || 1,
    params: {{ ...(h.params || {{}}), temperature: isNaN(temp) ? 0 : temp }},
  }};
  const res = await fetch(api("/api/harnesses/" + id), {{
    method: "PUT", headers: {{"Content-Type":"application/json"}}, body: JSON.stringify(payload),
  }}).then(r => r.json());
  if (res.ok) {{
    harnesses[id] = res.harness;
    renderHarnesses();
    renderPool();
    renderStaff();
    closeModal("modal-harness");
  }}
}}

// ── Templates ──────────────────────────────────────────────────────────────

let templates = [];

const TYPE_LABELS = {{
  ha_dashboard: "HA Dashboard",
  python_addon: "Python Addon",
  yaml_config: "YAML Config",
  code_project: "Code Project",
  instruction: "Instruction",
  custom: "Custom",
}};

function templateCard(t) {{
  const isBuiltin = !!t._builtin;
  const bodyPreview = t.body ? `<div style="font-size:0.7rem;color:#334155;margin-top:0.35rem;font-style:italic;white-space:pre-wrap;max-height:3.5rem;overflow:hidden">${{t.body.slice(0,180)}}${{t.body.length > 180 ? "…" : ""}}</div>` : "";
  return `
  <div class="tmpl-card" style="${{isBuiltin ? "border-left:3px solid #334155" : ""}}">
    <div style="display:flex;align-items:center;gap:0.4rem">
      <span class="tmpl-name">${{t.name || "Untitled"}}</span>
      ${{isBuiltin ? '<span style="font-size:0.62rem;color:#334155;border:1px solid #334155;border-radius:3px;padding:0.1rem 0.3rem">built-in</span>' : ""}}
    </div>
    <div class="tmpl-desc">${{t.description || "No description."}}</div>
    ${{bodyPreview}}
    <div class="tmpl-foot">
      <span class="tmpl-type">${{TYPE_LABELS[t.type] || t.type || "—"}}</span>
      ${{t.author ? `<span style="font-size:0.68rem;color:#374151">by ${{t.author}}</span>` : ""}}
      <button class="btn btn-ghost btn-sm" style="margin-left:auto"
        onclick="useTemplate('${{t.id}}')">Use</button>
      ${{!isBuiltin ? `<button class="btn btn-ghost btn-sm" style="font-size:0.7rem;color:#374151" onclick="deleteTemplate('${{t.id}}')">✕</button>` : ""}}
    </div>
  </div>`;
}}

function workerConfigCard(w) {{
  const h = Object.values(harnesses).find(h => h.model === w.model) || {{}};
  const caps = h.capabilities || [];
  const statusCls = w.status === "Configured" ? "status-ongoing" : w.status === "Disabled" ? "status-done" : "status-halted";
  return `
  <div class="tmpl-card" style="border-left:3px solid #6366f1">
    <div style="display:flex;align-items:center;gap:0.4rem;flex-wrap:wrap">
      <span class="tmpl-name">${{w.name || "Worker " + w.id}}</span>
      <span class="status-badge ${{statusCls}}">${{w.status}}</span>
    </div>
    <div class="tmpl-desc" style="font-family:monospace;font-size:0.72rem">
      ${{w.model || "—"}}<br>
      ${{w.request_format}} · ${{w.provider}}<br>
      ${{w.endpoint || "no endpoint"}}
    </div>
    ${{caps.length ? `<div style="display:flex;gap:0.3rem;flex-wrap:wrap;margin-top:0.3rem">${{caps.map(c=>`<span class="cap-tag">${{c}}</span>`).join("")}}</div>` : ""}}
    ${{h.notes ? `<div style="font-size:0.68rem;color:#334155;margin-top:0.3rem;font-style:italic">${{h.notes}}</div>` : ""}}
    <div class="tmpl-foot">
      ${{h.context_window ? `<span style="font-size:0.68rem;color:#475569">ctx ${{h.context_window >= 1000 ? (h.context_window/1000).toFixed(0)+"k" : h.context_window}}</span>` : ""}}
      ${{h.cost_type ? `<span class="cost-badge ${{h.cost_type === "local" ? "local" : "cloud"}}">${{h.cost_type}}</span>` : ""}}
      ${{h.reasoning ? `<span style="font-size:0.68rem;color:#818cf8">reasoning ✓</span>` : ""}}
    </div>
  </div>`;
}}

function renderWorkerConfigs() {{
  const el = document.getElementById("tmpl-grid");
  if (!configuredWorkers.length) {{
    el.innerHTML = '<div style="color:#475569;font-size:0.85rem;padding:0.5rem 0;grid-column:1/-1">No workers configured.</div>';
    return;
  }}
  el.innerHTML = configuredWorkers.map(workerConfigCard).join("");
}}

async function loadTemplates() {{
  const res = await fetch(api("/api/fleet/templates")).then(r => r.json());
  templates = (res.templates || []).filter(t => !t._builtin);
  renderWorkerConfigs();
  renderTemplates();
}}

function renderTemplates() {{
  const el = document.getElementById("job-tmpl-grid");
  if (!templates.length) {{
    el.innerHTML = '<div style="color:#475569;font-size:0.85rem;padding:0.5rem 0;grid-column:1/-1">No job templates yet.</div>';
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
