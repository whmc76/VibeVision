# VibeVision

VibeVision is an AI generation service for bot-first user workflows. The initial channel is Telegram: users send natural-language requests and media to a bot, the service understands intent with the configured LLM provider, selects the best matching active workflow, dispatches the job to ComfyUI, and tracks memberships and credit usage.

## Scope

- Telegram bot webhook entrypoint.
- Configurable LLM routing plus prompt enhancement, with MiniMax M2.7 or local Ollama and optional separate logic and prompt models.
- ComfyUI workflow dispatch for image generation, editing, and image-to-video jobs.
- Membership and credit ledger primitives.
- Admin API for users, jobs, workflows, and credit adjustments.
- Modern operator UI for user and task management.

## Project Layout

```text
backend/    FastAPI service, database models, bot orchestration, integrations
config/     Unified local ports and service endpoints
frontend/   Vite React admin workspace
docs/       Architecture notes and operating assumptions
scripts/    PowerShell launch scripts that read config/vibevision.env and local overrides
```

## Quick Start

All local ports are configured in `config/vibevision.env`. Put private overrides such as `TELEGRAM_BOT_TOKEN` and `MINIMAX_API_KEY` in ignored `config/vibevision.local.env`. Ollama uses its standard local port when selected, while VibeVision services use a less common range:

- API: `http://localhost:18751`
- Admin frontend: `http://localhost:18742`
- Ollama: `http://localhost:11434`
- ComfyUI: `http://localhost:8401`

One-click Windows bootstrap:

```powershell
.\start-vibevision.bat
```

This entrypoint checks local config, installs/syncs backend dependencies with `uv`, installs frontend dependencies with `npm` when needed, validates ComfyUI/LLM configuration, starts missing VibeVision services without restarting existing listeners, checks external service status, and opens the local control GUI. Use `.\start-vibevision.bat -Repair` to force dependency repair, or `.\start-vibevision.bat -NoGui` to only start and print status.

Backend:

```powershell
cd backend
uv sync --extra dev
cd ..
.\scripts\start-backend.ps1
```

The backend is managed with `uv`. Use `uv run` for local Python tooling:

```powershell
cd backend
uv run ruff check app
uv run python -m compileall app
uv run pytest
```

Frontend:

```powershell
cd frontend
npm install
cd ..
.\scripts\start-frontend.ps1
```

ComfyUI backend service:

```powershell
.\scripts\start-comfyui.ps1
```

This starts the ComfyUI HTTP backend from `COMFYUI_ROOT` and does not open the ComfyUI browser UI.

Start or stop the local VibeVision service group:

```powershell
.\scripts\start-all.ps1
.\scripts\stop-all.ps1
```

ComfyUI and Ollama are treated as external services. Start or restart them explicitly when needed:

```powershell
.\scripts\restart-comfyui.ps1
.\scripts\restart-ollama.ps1
```

Local service monitor GUI:

```powershell
.\scripts\vibevision-control.ps1
```

The control window can start missing VibeVision background services when it opens, stop VibeVision services when it exits, refresh service status automatically, and show timestamped operation output in its terminal pane. The three main controls are `Start/Restart VibeVision`, `Start/Restart ComfyUI`, and `Start/Restart Ollama`; ComfyUI and Ollama are not started or stopped by the default VibeVision start/stop flow.

If your local ComfyUI still runs on its standard port, change only `COMFYUI_PORT` in `config/vibevision.env`.

## Local Services

Configured local defaults:

- Ollama: `http://localhost:11434`
- ComfyUI: `http://localhost:8401`
- API: `http://localhost:18751`
- Frontend: `http://localhost:18742`

Default LLM role split:

```powershell
LLM_PROVIDER=minimax
LLM_LOGIC_PROVIDER=minimax
LLM_PROMPT_PROVIDER=ollama
LLM_VISION_PROVIDER=minimax_mcp
MINIMAX_BASE_URL=https://api.minimaxi.com/v1
MINIMAX_API_HOST=https://api.minimaxi.com
MINIMAX_MODEL=codex-MiniMax-M2.7
MINIMAX_LOGIC_MODEL=codex-MiniMax-M2.7
OLLAMA_PROMPT_MODEL=huihui_ai/qwen3.5-abliterated:9b
OLLAMA_MAX_CONCURRENCY=1
COMFYUI_MAX_CONCURRENCY=1
GPU_IDLE_RELEASE_SECONDS=5
TELEGRAM_POLLER_MAX_WORKERS=4
```

Put `MINIMAX_API_KEY` in ignored `config/vibevision.local.env`. The MiniMax logic path uses the OpenAI-compatible `/chat/completions` API, matching the hosted M2.7 coding-plan setup. Vision understanding uses the MiniMax Coding Plan MCP-compatible `/v1/coding_plan/vlm` endpoint. Prompt enhancement uses local Ollama qwen3.5 9b by default so prompt expansion has a dedicated local context budget.
When Ollama or ComfyUI has no active VibeVision task for `GPU_IDLE_RELEASE_SECONDS`, the backend unloads local models and asks ComfyUI to free GPU memory.

Optional MiniMax model split:

- `MINIMAX_LOGIC_MODEL` controls workflow routing and parameter inference.
- `MINIMAX_PROMPT_MODEL` controls prompt enhancement before generation.
- If either is blank, VibeVision falls back to `MINIMAX_MODEL`.

Legacy/local Ollama model:

```powershell
ollama pull huihui_ai/qwen3.5-abliterated:9b
```

Optional Ollama model split:

- `OLLAMA_LOGIC_MODEL` controls workflow routing and parameter inference.
- `OLLAMA_PROMPT_MODEL` controls prompt enhancement before generation.
- If either is blank, VibeVision falls back to `OLLAMA_MODEL`, so the current default still uses one shared model.

The backend ships with SQLite for local development. Use `DATABASE_URL` to point at Postgres when moving toward production.

## Credit Pricing

VibeVision uses one simple task ratio across the bot and admin console:

- Image task: `1` credit.
- Video task: `10` credits.
- Daily bonus credits reset at midnight and do not accumulate.
- Generation consumes daily bonus credits first, then paid credits.

Account ladder:

| Identity | Rule | Recharge multiplier |
| --- | --- | ---: |
| Guest | Default identity, receives 5 initial credits | 1.0x |
| Member | Any recharge promotes the user; first recharge adds 30 bonus credits | 1.0x |
| VIP | Cumulative recharge reaches USD 100 | 1.1x |
| SVIP | Cumulative recharge reaches USD 500 | 1.2x |

Recharge products:

| Monthly product | Price | Paid credits | Daily bonus |
| --- | ---: | ---: | ---: |
| Monthly subscription | USD 9.9/month | 100 | 10/day |
| Premium subscription | USD 29.9/month | 330 | 30/day |

## Telegram MVP Loop

For local development, run Telegram in local polling mode. This does not require a public URL, tunnel, reverse proxy, or deployed webhook service:

```powershell
.\scripts\start-telegram-poller.ps1
```

The poller disables any registered webhook and then listens with Telegram `getUpdates`, so inbound messages work from a local machine behind a normal router as long as the machine can make outbound HTTPS requests to Telegram.

The `/api/telegram/webhook` endpoint is still available when you explicitly want webhook delivery. Both modes share the same message processing loop:

1. Parse Telegram text, caption, image, document, video, or animation messages.
2. Resolve uploaded media with Telegram `getFile`.
3. Route the request with the configured LLM logic model against the active workflow catalog, using MiniMax Coding Plan MCP vision for attached images by default, then enhance the final generation prompt with the prompt model; image inputs are sent to Ollama VLM only when `LLM_VISION_PROVIDER=ollama`, with fallback workflow selection if the LLM is unavailable.
4. Reserve credits and submit the selected workflow to ComfyUI.
5. Poll ComfyUI history until outputs appear or the configured timeout is reached.
6. Upload generated media back to the Telegram chat and update task status.

Set `TELEGRAM_BOT_TOKEN` in ignored `config/vibevision.local.env`.

Webhook mode is optional. If you need it, expose the API with your preferred tunnel and register:

```powershell
$token = "<bot-token>"
$url = "https://your-public-domain.example/api/telegram/webhook"
Invoke-RestMethod "https://api.telegram.org/bot$token/setWebhook" -Method Post -Body @{ url = $url }
```

You can also inspect or manage the webhook with the bundled helper:

```powershell
.\scripts\telegram-webhook.ps1 -Action info
.\scripts\telegram-webhook.ps1 -Action set -PublicBaseUrl "https://your-public-domain.example"
.\scripts\telegram-webhook.ps1 -Action delete
```

The high-frequency service monitor keeps Telegram checks local-only for responsiveness. Use the webhook helper when you need live Telegram registration diagnostics.

For image or video generation to complete, ComfyUI must be reachable at the host and port configured in `config/vibevision.env`. This workspace is configured for the local ComfyUI folder `E:\ComfyUI_Feb` and port `8401`.

The default `image.edit` route is wired to `backend/app/workflow_templates/flux2klein_single_edit_api.json`, converted from `Flux2Klein_SingleEdit.json`. It expects the matching ComfyUI custom nodes and model files:

- `klein_miracleinNSFWGeneration_10Nvfp4.safetensors`
- `qwen_3_8b_fp4mixed.safetensors`
- `flux2-vae.safetensors`
