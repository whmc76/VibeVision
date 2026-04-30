from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.schemas import BotMessageRequest, BotMessageResponse
from app.services.credits import InsufficientCreditsError
from app.services.error_details import append_error_detail
from app.services.intent import TargetOutputRequiredError
from app.services.orchestrator import GenerationOrchestrator, WorkflowUnavailableError
from app.services.task_runner import process_telegram_update
from app.services.telegram_update_queue import TelegramUpdateQueue

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/message", response_model=BotMessageResponse)
async def submit_bot_message(
    payload: BotMessageRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BotMessageResponse:
    orchestrator = GenerationOrchestrator(settings)
    try:
        task = await orchestrator.enqueue_bot_message(db, payload)
    except WorkflowUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TargetOutputRequiredError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InsufficientCreditsError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    return BotMessageResponse(
        task_id=task.id,
        status=task.status,
        kind=task.kind,
        credit_cost=task.credit_cost,
        remaining_credits=task.user.credit_balance,
        message=(
            "Task queued."
            if not task.error_message
            else append_error_detail(
                "Task created but queueing failed.",
                task.error_message,
                task_id=task.id,
            )
        ),
    )


@router.post("/webhook")
async def telegram_webhook(
    update: dict,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool | str]:
    if settings.telegram_webhook_secret and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret.")

    if not settings.telegram_bot_token:
        raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN is not configured.")

    update_queue = TelegramUpdateQueue(settings)
    if update_queue.enabled:
        try:
            await update_queue.enqueue(update)
        finally:
            await update_queue.close()
        return {
            "ok": True,
            "message": "Webhook queued.",
        }

    background_tasks.add_task(process_telegram_update, update, settings)
    return {
        "ok": True,
        "message": "Webhook accepted.",
    }
