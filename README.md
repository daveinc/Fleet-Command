# Fleet Command

Fleet Command is a Home Assistant add-on that orchestrates a multi-stage AI worker pipeline to generate dashboards, YAML configs, and other outputs.

## What It Does

Runs a configurable pipeline of AI workers — Project Manager → Manager → Generator → Reviewer → Supervisor — each assigned to a model of your choice (local Ollama, Claude, OpenAI-compatible, or any custom HTTP endpoint).

## What's New in v1.5.1

- **Universal chunking** — messages are split to fit each worker's context window. Nothing is ever truncated.
- **Python-only assembler** — dashboard fragments assembled in Python, no AI worker needed for that stage.
- **3-pass reviewer** — entity resolution, structure review, and card_mod validation as separate chunked passes.
- **Escalation system** — workers signal `ESCALATE: <reason>` when stuck; the level above provides guidance and the worker retries.
- **Modelfile support** — per-harness Ollama Modelfiles stored and pushed from the UI.
- **Harness probe** — test connection and auto-retrieve context window before saving a new worker.
- **Role minimums** — configure recommended context window / token allowance per role. Harness cards flag workers that fall below the minimum.
- **Auto context window resolution** — Ollama harnesses query `/api/show` at load time to resolve their real context window.

## What's New in v1.5.1

- **Modelfile generator** — generate a complete Ollama Modelfile per harness from the UI. Fetches existing Modelfile from Ollama as base, merges in role persona, chain-of-command rules, escalation signal format, per-role parameters (repeat_penalty, top_k, top_p, seed), stop sequences, and few-shot MESSAGE examples. Editable before push. Warns before overwriting a previously pushed Modelfile.
- **Token allowance respected in chunking** — `available_input_tokens` now reserves `max(min_output_tokens, token_allowance)` so input chunks are correctly sized for workers with large output budgets.
- **Role minimum tags** — harness capability tags now only show roles the worker actually meets the context window minimum for.
- **Role minimum warnings on role cards** — assigned worker flagged with ⚠ if it falls below the minimum for that role.
- **Dropdown cleaned up** — role assignment picker shows clean model names only, no capability warnings in the list.

## Pipeline Stages

| Stage | Role | Purpose |
|---|---|---|
| Project Manager | Planning | Breaks job spec into blocks and card list |
| Manager | Task breakdown | Converts plan into one task per card |
| Generator | Code / YAML | Produces one card per task |
| Assembler | Python | Combines fragments into a full dashboard |
| Reviewer | QA (3-pass) | Entity resolution → structure → card_mod |
| Supervisor | Sign-off | Validates against spec, routes rejections |

## Installation

1. Add this repository to your Home Assistant add-on store.
2. Install and start the **Fleet Command** add-on.
3. Open the Fleet Command panel via the sidebar.
4. Assign AI models to roles in the **Staff** tab.
5. Submit a job in the **Projects** tab.

## Add-on folder

The add-on source lives in [`fleet-command/`](./fleet-command/).
