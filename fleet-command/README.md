# Fleet Command Add-on

Fleet Command runs a small status API inside Home Assistant.

It exposes:

- `GET /capabilities`
- `GET /status`
- `GET /workers`
- `POST /workers/{worker_id}/test`

Use the **Fleet Command** custom integration to turn those endpoints into HA sensors.

## Setup

1. Install and start this add-on.
2. Install the `custom_components/fleet_command` integration.
3. Add the integration in Home Assistant.
4. Use host `127.0.0.1` and port `8765`.

Ingress is enabled for the add-on page. The direct API port is optional and disabled unless you assign a host port in the Network tab.

## Worker Configuration

The add-on settings include four configurable AI worker slots. Each slot can target a local or hosted worker, including this kind of setup:

- local Ollama on another machine, for example `http://192.168.1.50:11434` + `/api/generate`
- OpenAI/Codex-style API, for example `https://api.openai.com` + `/v1/responses`
- OpenAI-compatible server, for example `/v1/chat/completions`
- Anthropic-style endpoint, for example `/v1/messages`
- any custom HTTP endpoint that accepts `{ "model": "...", "prompt": "..." }`

Fields per worker:

- enabled
- name
- role
- provider
- base URL
- API path
- model
- request format
- auth type
- custom auth header
- API key

API keys are stored in add-on options and are never exposed in `/status`, `/workers`, or HA sensor attributes. Those endpoints only show `has_api_key: true/false`.
