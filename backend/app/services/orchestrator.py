from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import GenerationTask, LedgerReason, TaskStatus, User, Workflow
from app.schemas import BotMessageRequest
from app.services.comfyui import ComfyUIClient
from app.services.credits import adjust_credits, reserve_for_task
from app.services.intent import IntentService


class WorkflowUnavailableError(ValueError):
    pass


class GenerationOrchestrator:
    def __init__(self, settings: Settings):
        self.intent = IntentService(settings)
        self.comfyui = ComfyUIClient(settings)

    async def handle_bot_message(self, db: Session, payload: BotMessageRequest) -> GenerationTask:
        user = self._get_or_create_user(db, payload)
        intent = await self.intent.classify(
            payload.text,
            has_image=bool(payload.source_media_url),
            source_media_url=payload.source_media_url,
        )
        workflow = self._select_workflow(db, intent.kind)

        task = GenerationTask(
            user_id=user.id,
            workflow_id=workflow.id,
            kind=intent.kind,
            status=TaskStatus.queued,
            original_text=payload.text,
            interpreted_prompt=intent.prompt,
            source_media_url=payload.source_media_url,
            credit_cost=workflow.credit_cost,
            telegram_chat_id=payload.telegram_chat_id,
            telegram_message_id=payload.telegram_message_id,
        )
        db.add(task)
        db.flush()

        reserve_for_task(db, user, workflow.credit_cost, task_id=task.id)

        try:
            task.external_job_id = await self.comfyui.submit_prompt(
                workflow=workflow,
                prompt=intent.prompt,
                source_media_url=payload.source_media_url,
            )
            task.status = TaskStatus.running
        except Exception as exc:
            task.status = TaskStatus.failed
            task.error_message = str(exc)
            adjust_credits(
                db=db,
                user=user,
                amount=workflow.credit_cost,
                reason=LedgerReason.task_refunded,
                note="Refunded after ComfyUI dispatch failure.",
                task_id=task.id,
            )

        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def _get_or_create_user(self, db: Session, payload: BotMessageRequest) -> User:
        user = db.scalar(select(User).where(User.telegram_id == payload.telegram_id))
        if user:
            if payload.username:
                user.username = payload.username
            if payload.display_name:
                user.display_name = payload.display_name
            db.add(user)
            db.flush()
            return user

        user = User(
            telegram_id=payload.telegram_id,
            username=payload.username,
            display_name=payload.display_name,
        )
        db.add(user)
        db.flush()
        return user

    def _select_workflow(self, db: Session, kind):
        workflow = db.scalar(
            select(Workflow)
            .where(Workflow.kind == kind, Workflow.is_active.is_(True))
            .order_by(Workflow.id)
        )
        if not workflow:
            raise WorkflowUnavailableError(f"No active workflow registered for {kind}.")
        return workflow
