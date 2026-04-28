from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.schemas import BotMessageRequest, BotMessageResponse
from app.services.credits import InsufficientCreditsError
from app.services.orchestrator import GenerationOrchestrator, WorkflowUnavailableError

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/message", response_model=BotMessageResponse)
async def submit_bot_message(
    payload: BotMessageRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BotMessageResponse:
    orchestrator = GenerationOrchestrator(settings)
    try:
        task = await orchestrator.handle_bot_message(db, payload)
    except WorkflowUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InsufficientCreditsError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    return BotMessageResponse(
        task_id=task.id,
        status=task.status,
        kind=task.kind,
        credit_cost=task.credit_cost,
        remaining_credits=task.user.credit_balance,
        message="Task accepted." if not task.error_message else "Task created but dispatch failed.",
    )


@router.post("/webhook")
async def telegram_webhook(
    update: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool | str]:
    if settings.telegram_webhook_secret and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret.")

    return {
        "ok": True,
        "message": "Webhook received. Map Telegram updates to /telegram/message in the next integration step.",
    }
