# Fleet Command — Architecture & Vision

## The One-Line Summary

Claude drops a job file → Fleet Command runs the entire chain autonomously → HA notifies when done → Claude checks result. Claude never babysits.

---

## What This Builds Toward

A HA addon that accepts a job spec from Claude and delivers finished software — no human
intervention between request and result. Output types in order of priority:

1. **HA dashboard YAML** — inserted directly into a live HA dashboard
2. **Python** — HA addons, integrations, scripts
3. **Windows apps** — delivered as files via /www
4. **Linux apps** — ultimate goal, tackled last

---

## Confirmed Worker Roster (tested, proven)

| Model | Role | Location | Context | Token limit | Notes |
|---|---|---|---|---|---|
| qwen-ha:1.5b | Generator | Local Ollama | ~4k practical | Unlimited | Proven on 15 YAML patterns. temp=0. Custom Modelfile. |
| gemma4:e4b | Manager + Reviewer | Local Ollama | Large | Unlimited | Matches cloud quality. No concurrency limits. Free. |
| gpt-oss:120b-cloud | Supervisor / Top Reviewer | Ollama cloud | Very large | Shared pool — unknown limit | Reasoning modes (think: true). Best quality available. Use sparingly. |
| gemma4:31b-cloud | Reviewer fallback | Ollama cloud | 128k | Shared pool | Sequential only — 500 on concurrent requests. |
| qwen2.5-coder:1.5b | General coder worker | Local Ollama | ~4k | Unlimited | Not YAML-specialized. |
| OpenCode | Worker / Reviewer | App (2 instances) | TBD | TBD | Coming soon — doubles workforce capacity. |
| Codex | High-quality reviewer / writer | API | TBD | Pool-based | Coming soon — use sparingly. |

**Cloud token pool:** All cloud models (gpt-oss, gemma:cloud) are assumed to share one Ollama token pool. Limit unknown. Test sparingly until confirmed.

---

## Role Hierarchy

```
Claude (client — fires job and forgets)
  ↓  job spec file (small, structured)
Fleet Command addon (orchestrator — owns everything from here)
  ↓
Project Manager AI  [gpt-oss:120b-cloud]
  — expands job spec into full build plan
  — breaks plan into manager-sized chunks
  — monitors manager token budget
  — final assembly + validation
  ↓
Manager AI  [gemma4:e4b]
  — owns one module/block
  — provides each worker a task + blueprint sized to worker's context window
  — assembles module from worker outputs
  — reviews assembled module
  ↓
Generator Workers  [qwen-ha:1.5b, qwen2.5-coder:1.5b, OpenCode]
  — produce code/YAML for one assigned task
  — stateless — given everything they need per call
  ↓
Reviewer AI  [gemma4:e4b local, gemma4:31b-cloud, gpt-oss:120b-cloud]
  — validates output (syntax, structure, domain correctness)
  — can act as temporary manager or writer when needed
  — returns corrected output or REJECT with reason
  ↓
Fleet Command addon
  — pushes final result to target (HA API / /www / file)
  — sends HA notification: job done
```

---

## Fire-and-Forget Pattern (locked in)

1. Claude writes a job spec (JSON file or POST to `/job`)
2. Fleet Command picks it up, assigns job ID
3. Chain runs fully autonomously — no Claude involvement
4. HA sensor updates show progress in real-time
5. HA notification fires when done
6. Claude (or Dave) checks `/job/{id}/result`

Claude does not stay connected. Claude does not supervise. Fleet Command owns the chain.

---

## Context Window Management

Every worker has a known/estimated context window. Manager's job is to never overflow it.

- Manager tracks token count before each dispatch
- Blueprint/reference injected per task sized to: `worker_context_limit - task_overhead`
- If task too large → Manager splits further before dispatching
- Manager's own context monitored by Project Manager
- When Manager context approaches limit: checkpoint, summarize, fresh Manager instance
- gpt-oss:120b-cloud context is large but costs cloud tokens — use for supervision only, not generation

---

## HA Sensors (real-time monitoring)

| Sensor | What it shows |
|---|---|
| `sensor.fleet_job_status` | idle / planning / generating / reviewing / assembling / done / failed |
| `sensor.fleet_active_workers` | how many workers running right now |
| `sensor.fleet_job_progress` | 0–100% (blocks complete / total) |
| `sensor.fleet_manager_tokens_used` | manager's running token count |
| `sensor.fleet_manager_tokens_limit` | manager's context window cap |
| `sensor.fleet_last_result` | summary of last completed job |
| Per-worker | status, last response time, error count |

---

## Job Spec Format (Claude → Fleet Command)

```json
{
  "job_id": "groceries_main_v1",
  "output_type": "ha_dashboard_yaml",
  "target": {
    "dashboard": "groceries",
    "view": "main"
  },
  "blocks": [
    {
      "block_id": "B2",
      "template": "nav_bar",
      "entities": ["input_select.groceries_nav"],
      "options": { "tabs": ["main", "todo", "prices", "cart", "fridge"] }
    },
    {
      "block_id": "B3",
      "template": "status_cards",
      "entities": ["binary_sensor.grocy_expiring_products"],
      "options": {}
    }
  ]
}
```

- Template names map to reference library baked into addon
- Worker assignment handled by Fleet Command automatically based on role config
- `output_type` tells Fleet Command which delivery path to use

---

## Harness Store

Every AI model has a different API shape, context window, and token budget.
The addon stores a **harness** for each known model — a full profile of how to talk to it.
Harnesses are stored in `/data/harnesses/<model_slug>.json` and loaded at startup.

Each harness contains:

| Field | Example | Notes |
|---|---|---|
| `id` | `gemma4_e4b` | Unique slug |
| `display_name` | `Gemma 4 E4B (local)` | Shown in UI |
| `endpoint` | `http://host.docker.internal:11434` | Base URL |
| `api_path` | `/api/chat` | Request path |
| `request_format` | `ollama_chat` | How to build the request body |
| `auth_type` | `none` | none / bearer / x_api_key |
| `api_key` | `""` | Stored securely |
| `context_window` | `8192` | Known token limit. `null` = unknown |
| `token_allowance` | `null` | Per-session or total quota. `null` = unknown/unlimited |
| `cost_type` | `local` | local / cloud_shared / cloud_metered |
| `capabilities` | `["yaml","review","manage"]` | What this model is suited for |
| `reasoning` | `false` | Supports think: true / reasoning mode |
| `concurrency` | `1` | Max parallel calls. 1 = sequential only |
| `notes` | `"Sequential only — 500 on overlap"` | Human-readable warnings |
| `params` | `{"temperature": 0, "top_p": 0.9}` | Inference parameters sent with every request to this slot |

### Inference Parameters (`params`)

Each harness carries its own generation parameters — sent alongside every request.
These override model defaults and are fully configurable from the dashboard without a restart.

| Param | What it controls | Typical values |
|---|---|---|
| `temperature` | Creativity vs determinism | 0 = locked, 0.7 = creative, 1.0 = chaotic |
| `top_p` | Nucleus sampling cutoff | 0.9 default |
| `top_k` | Token candidate pool | 40 default |
| `repeat_penalty` | Stops repetition loops | 1.1 default |
| `num_predict` | Max tokens to generate | Model default if omitted |
| `stop` | Stop sequences | e.g. `["---"]` |

Slot assignments can also **override** harness params temporarily — e.g. promote gemma to manager role and bump its temperature to 0.7 for planning without touching the base harness. Override is stored in `role_assignments.json`, base harness stays clean.

Use cases:
- Generator slots: `temperature: 0` — deterministic YAML every time
- Manager/planner slot: `temperature: 0.5-0.7` — creative breakdown of tasks
- Reviewer slot: `temperature: 0` — consistent, predictable fixes
- Supervisor slot: `temperature: 0, think: true` — full reasoning, no drift

Built-in harnesses (shipped with addon):
- `qwen_ha_1_5b` — generator, YAML specialist, local, unlimited
- `gemma4_e4b` — manager + reviewer, local, unlimited
- `gpt_oss_120b_cloud` — supervisor, reasoning, cloud shared pool
- `gemma4_31b_cloud` — reviewer fallback, sequential, cloud shared pool
- `qwen2_5_coder_1_5b` — general coder, local, unlimited

User can add custom harnesses from the Fleet Command dashboard.

---

## Fleet Command Dashboard (Control Panel UI)

The addon's main page is a full fleet control panel. One screen — everything visible and controllable.

### Layout (top to bottom)

```
┌─────────────────────────────────────────────────────┐
│  FLEET COMMAND                        [Add Model +]  │
├─────────────────────────────────────────────────────┤
│  FLEET ROSTER — role order                          │
│                                                     │
│  ┌─ PROJECT MANAGER ───────────────────────────┐   │
│  │  [gpt-oss:120b-cloud ▼]  🟡 cloud           │   │
│  │  ctx: 128k  tokens: unknown  temp: 0.5      │   │
│  │  think: true                    [⚙] [↓]    │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  ┌─ MANAGER ───────────────────────────────────┐   │
│  │  [gemma4:e4b ▼]  🟢 local                   │   │
│  │  ctx: unlimited  temp: 0.5      [⚙] [↑][↓] │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  ┌─ GENERATOR ─────────────────────────────────┐   │
│  │  [qwen-ha:1.5b ▼]  🟢 local                 │   │
│  │  ctx: 4k  temp: 0               [⚙] [↑][↓] │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  ┌─ REVIEWER ──────────────────────────────────┐   │
│  │  [gemma4:e4b ▼]  🟢 local                   │   │
│  │  ctx: unlimited  temp: 0        [⚙] [↑][↓] │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  ┌─ SUPERVISOR ────────────────────────────────┐   │
│  │  [gpt-oss:120b-cloud ▼]  🟡 cloud           │   │
│  │  ctx: 128k  tokens: unknown     [⚙] [↑]    │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  AVAILABLE (unassigned)                             │
│  • qwen2.5-coder:1.5b — local, unlimited            │
│  • codex — [configure]                              │
│                                                [Save Chain]
├─────────────────────────────────────────────────────┤
│  ACTIVE JOB: groceries_main_v1                      │
│  [████████░░] 80%  Reviewing B3 filter buttons...   │
│  PM: idle  Manager: reviewing  Generator: idle      │
│  ──────────────────────────────────────────         │
│  12:34:01 Manager → qwen B3 step 1 (1.2s, 312 tok) │
│  12:34:03 Manager → qwen B3 step 2 (0.9s, 289 tok) │
│  12:34:05 Reviewer pass B3 step 1: PASS             │
└─────────────────────────────────────────────────────┘
```

### Role Slots

Each slot is a card. Collapsed = summary. Expanded = full worker picture.

```
┌─ MANAGER ───────────────────────────────────────────────────┐
│  [gemma4:e4b ▼]  🟢 local          [⚙] [↑][↓]  [▼ expand] │
│                                                              │
│  CTX  [████████████░░░░░░░░]  6,240 / 8,192 tokens  76%    │
│  TOK  unlimited                                             │
│                                                              │
│  STATUS  ● reviewing B3 step 2 of 4                         │
│                                                              │
│  TASK QUEUE                                                  │
│  ✅ step 1 — generate B2 nav bar           (1.2s, 289 tok)  │
│  ✅ step 2 — generate B3 status cards      (0.9s, 312 tok)  │
│  🔄 step 3 — review assembled B3 output    (in progress)    │
│  ⏳ step 4 — assemble final dashboard      (waiting)        │
│                                                              │
│  PARAMS  temp: 0.5  top_p: 0.9  ★ (override active)        │
└──────────────────────────────────────────────────────────────┘
```

- **CTX bar** — live token count in use vs known limit. If limit unknown: shows raw token count + "/ ?" and flags "context unknown — watch this slot"
- **TOK** — token allowance. Local = unlimited. Cloud = "limited / unknown" until empirically confirmed, then shows used/quota
- **STATUS** — what this worker is doing right now. Idle when no job running
- **TASK QUEUE** — every task assigned to this slot this job:
  - ✅ done (time + token cost)
  - 🔄 in progress (elapsed so far)
  - ⏳ queued (waiting)
  - ❌ failed (error summary)
- **PARAMS** — current effective params. ★ shown if slot overrides harness defaults
- [⚙] opens full param editor inline
- [↑][↓] promote/demote — swaps model with adjacent slot
- Dropdown — reassign model. Available harnesses shown with ctx size so you can see at a glance if the replacement is an upgrade or downgrade
- [Save Chain] persists current assignment + overrides to `/data/role_assignments.json`
- Chain survives restarts — permanent until changed

**Why the context bar matters:** if you're mid-job and the manager is at 90% context, you know before it fails. You can pause, checkpoint, and reassign a fresh instance or a model with a larger window — without losing the work already done.

### Add Model
- [Add Model +] button opens a form: name, endpoint, api_path, request_format, auth, context_window, params
- Saved as a new harness in `/data/harnesses/`
- Immediately available in all role slot dropdowns
- Codex, OpenCode instances added this way

### Available Pool
- Lists all harnesses not currently assigned to a role
- Shows capability tags, local/cloud, context window
- Can be dragged into a role slot or assigned via dropdown

### Live Job Monitor (bottom panel)
- Active job ID, status badge, progress bar
- Per-role status: idle / working / waiting
- Scrolling log: timestamp, who called who, response time, token count
- Stays visible while roster is shown — always know what the fleet is doing

### Context Window Display
- Known limit: shown as number (e.g. "4k", "128k")
- Unknown: shown as "?" 
- During a job: manager slot shows live running token count
- Cloud slots show token allowance as "limited" until empirically confirmed

---

## File Types — Three Distinct Concepts

### 1. Harness (`/data/harnesses/<slug>.json`)
HOW to call an AI — endpoint, format, auth, params. Already defined above.

### 2. Instruction File (`/data/instructions/<role_or_model>.md`)
WHAT to tell an AI — system prompt, rules, persona, behavior.
Like CLAUDE.md but per worker role or per model.

- `generator.md` — default instructions for any generator slot
- `manager.md` — default instructions for any manager slot
- `reviewer.md`, `supervisor.md`, `project_manager.md`
- `qwen_ha_1_5b.md` — model-specific overrides (injected on top of role instructions)
- Per project type: `templates/ha_dashboard/instructions/generator.md` overrides the global default

### 3. Project Template (`/data/templates/<name>/`)
A downloadable unit that defines a complete job type. No code changes needed to add new types.

```
/data/templates/ha_dashboard/
  template.json        — job spec format, steps, roles needed, output type, variables
  instructions/        — role instructions specific to this project type
    generator.md
    manager.md
    reviewer.md
  index/               — code knowledge for supervisor validation
    patterns.md        — card patterns + rules
    known_failures.md  — failure modes + fixes
```

User flow:
1. Download template zip from community / paste URL into addon
2. Addon extracts to `/data/templates/<name>/`
3. Template appears in job launcher — user fills in variables (entities, colors, etc.)
4. Fleet handles everything — no internals knowledge needed

### UI — Template Manager (future tab)
- List all installed templates with description + version
- Edit any instruction file inline (Monaco editor or textarea)
- Download template from URL / upload zip
- Delete template
- All harness + instruction edits apply on next job — no restart

---

## Supervisor Validation (Step 5)

The supervisor (gpt-oss:120b-cloud) validates the assembled output against two sources:

1. **Job spec** — did the output match what was requested?
   - Correct entities present?
   - Correct card types used?
   - Correct colors, options, layout values?
   - All requested blocks present?

2. **Code index** — does the output comply with known rules and patterns?
   - Dave has reference files (grocy-blocks.md, card encyclopedias, known patterns)
   - These get copied to the addon in a structured format (`/data/index/`)
   - Supervisor checks output against these — custom: prefix rules, pipe scalar rules, structural patterns, known failure modes
   - Think of it as a linter with domain knowledge

Supervisor verdict:
- `PASS` — output matches spec + passes index checks → deliver to HA
- `FAIL: [specific issues]` — list of exact problems found → back to manager for fixes
- `WARN: [notes]` — delivered but flagged for Dave's review

**Not a ground-truth comparison.** No "correct answer file" needed. Works for every job, not just known test cases. Ground-truth comparison is only used during our testing phase to validate the chain itself.

**Code index files** (Dave to provide — copied to `/data/index/` in addon):
- Card pattern encyclopedia (15 proven patterns + rules)
- Known failure modes + fixes
- HA-specific rules (custom: prefix list, pipe scalar requirement, container format rules)
- Per-project specs (e.g. grocy-blocks.md for the groceries dashboard)

---

## Reference Library

15 proven card patterns baked into addon Python as a dict — no external files at runtime.

Each entry:
- Pattern name + card type
- Complete reference YAML (the proven reference card, real values, no placeholders)
- Known failure modes + reviewer fixes
- Estimated token cost

Manager selects correct reference per generator task based on block's `template` field.

---

## Delivery Targets

| Output type | Delivery method |
|---|---|
| `ha_dashboard_yaml` | Addon calls HA API via python_transform. Backup before every push — non-negotiable. |
| `python_addon` | Written to `/www/fleet_output/<job_id>/` |
| `windows_app` | Written to `/www/fleet_output/<job_id>/` — user downloads from HA |
| `linux_app` | Written to `/www/fleet_output/<job_id>/` — future |

---

## Step-by-Step Build List

### Already done
- [x] Addon skeleton deployed in HA (v0.1.0, ingress, multi-arch)
- [x] qwen-ha:1.5b proven on 15 YAML patterns (temp=0, all PASS)
- [x] gemma4:e4b local proven as reviewer (R1, R3, R5 PASS — matches cloud quality)
- [x] gpt-oss:120b-cloud confirmed alive and responsive (R1, R3 PASS)
- [x] Ollama models moved to D:\AI\ollama (symlinked, pip/npm cache redirected)
- [x] Architecture doc written

### Current — validate the chain theory (no app yet)
- [ ] **Chain test script** — standalone Python, no app:
  - Claude gives a known request (B3 filter buttons — truth output exists)
  - gemma4:e4b (manager): breaks into steps, writes reference+task prompts
  - qwen-ha:1.5b (generator): produces YAML per step
  - gemma4:e4b (assembler/reviewer): combines + reviews all outputs
  - gpt-oss:120b-cloud (supervisor): checks assembled YAML against truth file, reports back
- [ ] Evaluate results — does the chain produce correct output without Claude guiding each step?
- [ ] Document what the manager prompt needs to contain to reliably instruct qwen

### Phase 2 — Fix addon generator call format
- [ ] Add `ollama_chat` request format to `workers.py` (sends `/api/chat` with messages array)
- [ ] Add `ollama_chat` to `config.json` schema select options
- [ ] Update worker 1 in HA config: model=`qwen-ha:1.5b`, format=`ollama_chat`, path=`/api/chat`
- [ ] Prove via `/workers/1/test` that addon calls qwen-ha correctly

### Phase 3 — Job intake
- [ ] `POST /job` endpoint — receives job spec JSON
- [ ] Assigns job ID, writes to `/data/runs/<job_id>/job.json`
- [ ] Returns `{job_id, status: "queued"}`
- [ ] `GET /job/{id}` — returns current status
- [ ] Background task runner picks up queued jobs

### Phase 4 — Generator dispatch
- [ ] Addon reads job blocks, selects reference from library per template
- [ ] Builds reference+task prompt for each block
- [ ] Calls generator worker (qwen-ha:1.5b) per block
- [ ] Stores raw YAML in `/data/runs/<job_id>/blocks/<block_id>/raw.yaml`

### Phase 5 — Reviewer pass
- [ ] Each raw block YAML sent to reviewer worker (gemma4:e4b)
- [ ] Reviewed YAML stored in `/data/runs/<job_id>/blocks/<block_id>/reviewed.yaml`
- [ ] Retry once on failure, flag if still fails

### Phase 6 — Assembly
- [ ] Manager worker (gemma4:e4b or gpt-oss) receives all reviewed blocks
- [ ] Assembles into single dashboard YAML
- [ ] Stores in `/data/runs/<job_id>/result.yaml`

### Phase 7 — HA push
- [ ] Addon reads result.yaml
- [ ] Calls `ha_config_get_dashboard` → backs up to `/data/runs/<job_id>/backup.json`
- [ ] Pushes via python_transform (surgical edit only — never replace full config)
- [ ] Updates job status to `done`
- [ ] Fires HA notification

### Phase 8 — Reference library baked in
- [ ] All 15 card patterns coded into addon as Python dict
- [ ] Prompt builder selects correct reference by template name
- [ ] Generator always gets proper reference+task structure

### Phase 9 — HA sensors
- [ ] Addon exposes job status, progress, token counts as sensor endpoints
- [ ] HA integration polls and creates sensors
- [ ] Dashboard shows live build progress

### Phase 10 — Project Manager role
- [ ] gpt-oss:120b-cloud slot wired as project_manager role
- [ ] PM receives raw job spec, expands into full plan
- [ ] PM monitors manager token budget, checkpoints when needed
- [ ] Full autonomous plan-execute-deliver cycle tested end to end

### Phase 11 — OpenCode integration
- [ ] 2 OpenCode instances configured as worker slots
- [ ] Worker pool selection logic handles multiple generators
- [ ] Parallel block generation tested

### Phase 12+ — Future
- [ ] Codex as high-quality reviewer/writer
- [ ] Python output type
- [ ] Windows app output type
- [ ] Linux app output type (ultimate goal)

---

## Proven Rules — Never Break These

| Rule | Why |
|---|---|
| Never send emoji to generator models | Anchors model to reference content — causes copying not generating |
| Generator prompt = reference + `\n\n---\n\n` + task, user role only | Modelfile system prompt is baked in — separate system role overrides it |
| Reference must use same element type as task | "NOT state-icon" in task is not enough — model keeps reference elements |
| "Change ALL values" when reference and task share identical card structure | Prevents model anchoring to reference content |
| view_layout as nested YAML keys in task prompt, never inline | Inline gets echoed literally |
| Always backup dashboard before push | Non-negotiable — a full config replace destroyed a dashboard on 2026-05-02 |
| Reviewer: retry once on 500 after 3s, return unreviewed if still fails | gemma cloud is sequential — overlapping calls cause 500s |
| gemma needs custom: prefix list injected in system prompt | Has no domain knowledge of which HA cards are custom |
| Strip emoji to ASCII before every Ollama call | UnicodeEncodeError cp1252 crash on Windows |
