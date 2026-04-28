# VibeVision

VibeVision is an AI generation service for bot-first user workflows. The initial channel is Telegram: users send natural-language requests and media to a bot, the service interprets intent with local Ollama, routes generation/edit/video jobs to ComfyUI workflows, and tracks memberships and credit usage.

## Scope

- Telegram bot webhook entrypoint.
- Local LLM intent parsing through Ollama.
- ComfyUI image, edit, and image-to-video workflow dispatch.
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

All local ports are configured in `config/vibevision.env`. Put private overrides such as `TELEGRAM_BOT_TOKEN` in ignored `config/vibevision.local.env`. The default ports intentionally use a less common range:

- API: `http://localhost:18741`
- Admin frontend: `http://localhost:18742`
- Ollama: `http://localhost:18743`
- ComfyUI: `http://localhost:18744`

Backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cd ..
.\scripts\start-backend.ps1
```

Frontend:

```powershell
cd frontend
npm install
cd ..
.\scripts\start-frontend.ps1
```

If your local Ollama or ComfyUI still runs on its standard port, change only `OLLAMA_PORT` or `COMFYUI_PORT` in `config/vibevision.env`.

## Local Services

Configured local defaults:

- Ollama: `http://localhost:18743`
- ComfyUI: `http://localhost:18744`
- API: `http://localhost:18741`
- Frontend: `http://localhost:18742`

The backend ships with SQLite for local development. Use `DATABASE_URL` to point at Postgres when moving toward production.
