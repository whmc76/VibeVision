# VibeVision Architecture

## Product Loop

1. A Telegram user sends text, image, or image plus instructions.
2. The webhook stores or resolves the user account.
3. The configured LLM provider inspects the request, selects the best matching active workflow, and expands the generation prompt in a separate enhancement step.
4. The orchestrator validates the selected workflow, reserves credits, and dispatches the matching ComfyUI prompt graph.
5. Credits are reserved before queueing the job.
6. ComfyUI receives the workflow prompt graph.
7. Results are written back to the task record and can be sent to Telegram.

## Backend Boundaries

- `routers/telegram.py`: channel-specific Telegram webhook handling.
- `routers/admin.py`: operator API for the management frontend.
- `services/orchestrator.py`: workflow catalog loading, credit reservation, and dispatch validation.
- `services/intent.py`: LLM workflow router, prompt enhancer, role-specific provider dispatch, and fallback selector.
- `services/concurrency.py`: process-local async semaphores for serializing constrained backends such as Ollama and ComfyUI.
- `services/comfyui.py`: ComfyUI prompt submission.
- `services/credits.py`: ledger-safe credit reservation and adjustment.

## Data Model

- `User`: Telegram identity, membership tier, credit balance, status.
- `CreditLedgerEntry`: immutable credit events.
- `Workflow`: registered task type with ComfyUI prompt template metadata.
- `GenerationTask`: user request, chosen workflow, status, credit cost, result URLs.

## Production Notes

- Keep webhook handling fast. Long work should move to a queue worker before production traffic.
- Store uploaded media in object storage, not local disk.
- Use signed admin authentication before exposing the frontend publicly.
- Add idempotency keys around Telegram updates and credit reservation.
- Keep ComfyUI workflow JSON templates versioned so old tasks remain explainable.
- Keep ports, local service endpoints, LLM role provider defaults, and backend concurrency limits centralized in `config/vibevision.env`; keep private API keys in ignored `config/vibevision.local.env`.
