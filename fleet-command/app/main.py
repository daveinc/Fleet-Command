from __future__ import annotations

import app.config as _config

_config.load()

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.snapshot import capabilities, status
from app.workers import configured_workers, test_worker
from app.harnesses import load_harnesses
from app.roles import load_roles, save_roles, swap_roles, ROLE_ORDER, ROLE_LABELS

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
    return {"roles": load_roles(), "order": ROLE_ORDER, "labels": ROLE_LABELS}


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

    .role-label {{
      font-size: 0.65rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #6366f1;
      min-width: 110px;
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
  </style>
</head>
<body>

<div class="header">
  <h1>Fleet <span>Command</span></h1>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('fleet', this)">Fleet</div>
  <div class="tab" onclick="switchTab('jobs', this)">Jobs</div>
  <div class="tab" onclick="switchTab('harnesses', this)">Harnesses</div>
  <div class="tab" onclick="switchTab('templates', this)">Templates</div>
</div>

<!-- ── Fleet tab ── -->
<div class="tab-panel active" id="tab-fleet">
  <div class="section-title">Fleet Roster</div>
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

<!-- ── Jobs tab ── -->
<div class="tab-panel" id="tab-jobs">
  <div class="section-title">Active &amp; Recent Jobs</div>
  <div id="jobs-list" style="color:#475569;font-size:0.85rem;padding:1rem 0">
    No jobs yet. Jobs will appear here once the pipeline is running.
  </div>
</div>

<!-- ── Harnesses tab ── -->
<div class="tab-panel" id="tab-harnesses">
  <div class="section-title">Registered Models</div>
  <div class="harness-grid" id="harness-grid"></div>
</div>

<!-- ── Templates tab ── -->
<div class="tab-panel" id="tab-templates">
  <div class="section-title">Project Templates</div>
  <div style="color:#475569;font-size:0.85rem;padding:1rem 0">
    Template system coming soon. Drop a template zip here to install a new project type (HA dashboard, Python addon, Windows app, etc.).
  </div>
</div>

<script>
const ROOT = "{root}";
const api = path => ROOT + path;

const ROLE_ORDER = ["project_manager", "manager", "generator", "reviewer", "supervisor"];
const ROLE_LABELS = {{
  project_manager: "Project Manager",
  manager: "Manager",
  generator: "Generator",
  reviewer: "Reviewer",
  supervisor: "Supervisor",
}};

let harnesses = {{}};
let roles = {{}};
let originalRoles = {{}};

async function load() {{
  const [hRes, rRes] = await Promise.all([
    fetch(api("/api/harnesses")).then(r => r.json()),
    fetch(api("/api/roles")).then(r => r.json()),
  ]);
  harnesses = hRes.harnesses || {{}};
  roles = rRes.roles || {{}};
  originalRoles = JSON.parse(JSON.stringify(roles));
  renderRoster();
  renderPool();
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

function renderRoster() {{
  const el = document.getElementById("roster");
  const assignedIds = new Set(
    ROLE_ORDER.map(r => roles[r]?.harness_id).filter(Boolean)
  );

  el.innerHTML = ROLE_ORDER.map((role, idx) => {{
    const assignment = roles[role] || {{}};
    const hid = assignment.harness_id || "";
    const h = harnesses[hid];
    const params = assignment.params || {{}};
    const temp = params.temperature ?? (h?.params?.temperature ?? 0);
    const hasModel = !!hid;

    const options = Object.entries(harnesses)
      .map(([id, info]) => `<option value="${{id}}" ${{id === hid ? "selected" : ""}}>${{info.display_name}}</option>`)
      .join("");

    const upBtn = idx > 0
      ? `<button class="btn btn-ghost btn-sm" onclick="swapRoles('${{role}}','${{ROLE_ORDER[idx-1]}}')" title="Promote">↑</button>`
      : "";
    const downBtn = idx < ROLE_ORDER.length - 1
      ? `<button class="btn btn-ghost btn-sm" onclick="swapRoles('${{role}}','${{ROLE_ORDER[idx+1]}}')" title="Demote">↓</button>`
      : "";

    return `
    <div class="role-card ${{hasModel ? "has-model" : "empty"}}" id="card-${{role}}">
      <div class="card-header">
        <div class="role-label">${{ROLE_LABELS[role]}}</div>
        <select class="model-select" onchange="onModelChange('${{role}}', this.value)">
          <option value="">— unassigned —</option>
          ${{options}}
        </select>
        ${{costBadge(h)}}
        <div class="card-actions">
          <button class="btn btn-ghost btn-sm" onclick="toggleParams('${{role}}')" title="Params">⚙</button>
          ${{upBtn}}
          ${{downBtn}}
        </div>
      </div>
      <div class="card-meta">
        <span>${{ctxLabel(h)}}</span>
        <span>temp: <b>${{temp}}</b></span>
        ${{h?.notes ? `<span style="color:#374151">${{h.notes.substring(0,60)}}${{h.notes.length>60?"…":""}}</span>` : ""}}
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
  }}).join("");
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

load();
</script>
</body>
</html>"""
