# WORK ORDER — Fleet Command Pipeline Visualization Dashboard

**Project:** Fleet Command HA Addon
**Feature:** Pipeline Visualization + Chain Editor + Escalation Controls
**Method:** One block at a time, one task at a time, test before moving on.

---

## Block A — Pipeline Rules Store
- [ ] A1: Create `pipeline_rules.json` schema + read/write helpers in new `app/pipeline_rules.py`
- [ ] A2: API endpoints: GET/POST `/api/pipeline-rules`
- [ ] A3: Test: rules survive restart, API returns correct data

## Block B — Pipeline Tab (bare canvas)
- [ ] B1: Add "Pipeline" tab button to nav
- [ ] B2: Job selector dropdown at top, loads selected job data
- [ ] B3: Render static nodes (boxes) in horizontal flow — no connections yet
- [ ] B4: Test: tab opens, nodes appear for a real job

## Block C — Node detail + connections
- [ ] C1: Each node shows model, status color, char count, expand output
- [ ] C2: SVG bezier lines connecting node ports with data-type labels
- [ ] C3: Live poll — nodes update status as job runs
- [ ] C4: Test: run a job, watch nodes light up in sequence

## Block D — Run controls
- [ ] D1: "Re-run from here" button per node — API endpoint to re-run a job from a given stage
- [ ] D2: Test: re-run generator, upstream outputs preserved, downstream re-executes

## Block E — Escalation rules editor
- [ ] E1: Rules panel below the canvas — lists current rules
- [ ] E2: Add/edit/toggle rule UI (threshold, route-to, action)
- [ ] E3: Wire rules into `pipeline.py` reviewer logic
- [ ] E4: Test: set threshold to 3 issues, run a bad job, verify escalation fires

## Block F — Chain editor
- [ ] F1: Add/remove stages from chain in the UI
- [ ] F2: Reorder stages (drag or up/down arrows)
- [ ] F3: Edit per-stage prompt template inline
- [ ] F4: Test: add a stage, run a job, confirm it executes

---

## Design Principles (for future addon templates)
- Build in blocks with explicit task breakdowns
- Each task is independently testable before moving to the next
- No task depends on an untested previous task
- UI controls and backend logic ship together per block
- Rules and config are stored in `/data/` — persistent, editable from UI
- Visualization and control live in the same panel — no split context

## Escalation Logic (agreed design)
- Reviewer: if issues <= threshold → fix inline and pass forward
- Reviewer: if issues > threshold → escalate to supervisor with summary
- Supervisor: decides re-plan (routes back to PM, full chain re-runs) or fix himself
- Supervisor rejection includes `REJECTED_AT: <stage>` to specify re-entry point
- Default re-entry if unspecified: generator (but full downstream chain re-runs)
- All escalation rules are configurable from the Pipeline tab

## Node Port Types (Blueprint-style)
- `send_to` — output to next stage
- `receive_from` — input from previous stage
- `get_data` — read stage output from storage
- `get_resources` — pull harness/model/prompt config
- Future: `if/or/XOR` logic nodes, loop-back connections
