# Fleet Command Add-on

Fleet Command is an AI pipeline orchestrator running inside Home Assistant. It manages a multi-stage worker pipeline (Project Manager → Manager → Generator → Reviewer → Supervisor) to generate Home Assistant dashboards and other YAML/code outputs.

## What's New in 1.5.0

- **Universal chunking** — all pipeline stages chunk messages to fit each worker's context window. Nothing is ever truncated.
- **Python-only assembler** — dashboard fragments are assembled in Python, no AI worker needed for that stage.
- **3-pass reviewer** — entity resolution, structure review, and card_mod validation run as separate chunked passes.
- **Escalation system** — workers signal `ESCALATE: <reason>` when stuck; the level above provides guidance and the worker retries.
- **Modelfile support** — per-harness Ollama Modelfiles stored and pushed from the UI. When a Modelfile is active, the system prompt is omitted from API calls (persona lives in the model).
- **Harness probe** — test connection and auto-retrieve context window before saving a new worker.
- **Role minimums** — configure recommended context window / token allowance per role. Harness cards flag workers that fall below minimums.
- **Auto context window resolution** — Ollama harnesses query `/api/show` at load time to resolve their real context window.

## Pipeline Stages

| Stage | Role | Purpose |
|---|---|---|
| Project Manager | Planning | Breaks job spec into blocks and card list |
| Manager | Task breakdown | Converts plan into one task per card |
| Generator | Code/YAML | Produces one card per task |
| Assembler | Python | Combines fragments into a full dashboard |
| Reviewer | QA (3-pass) | Entity resolution → structure → card_mod |
| Supervisor | Sign-off | Validates against spec, routes rejections |

## Setup

1. Install and start this add-on.
2. Open the Fleet Command panel (ingress).
3. Assign harnesses to roles in the Staff tab.
4. Submit a job in the Projects tab.

Direct API port `8765` is optional — leave unassigned unless you need external access.

## Harness Registry

Workers are defined as harnesses in the Staff tab. Built-in harnesses cover:
- Local Ollama models (`qwen-ha:1.5b`, `gemma4:e4b`, `qwen2.5-coder:1.5b`)
- Cloud Ollama (`gpt-oss:120b-cloud`, `gemma4:31b-cloud`)
- Anthropic API (`claude-sonnet-4-6`, `claude-opus-4-7`)

Custom harnesses can be added via the **New Worker** button. Use **Test Connection** to verify and auto-fill context window before saving.

## Role Minimums

In the Templates tab, configure minimum context window and token allowance per role. Harness capability tags show a warning (⚠) if a worker falls below the minimum for that role. Not enforced — informational only.

## API

- `GET /api/harnesses` — list all harnesses
- `POST /api/harnesses/probe` — test a harness config
- `GET /api/roles` — current role assignments
- `GET /api/role-minimums` — role minimum config
- `GET /api/jobs` — list jobs
- `POST /api/jobs` — create and run a job
- `GET /capabilities` — add-on capabilities
- `GET /status` — legacy sensor feed (rebuild planned)
