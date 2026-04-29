import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import Settings
from app.db.session import SessionLocal
from app.models import GenerationTask, TaskStatus
from app.services.comfyui import ComfyUIClient
from app.services.concurrency import concurrency_slot
from app.services.credits import refund_task_credits
from app.services.error_details import format_exception_details
from app.services.task_runner import complete_comfyui_task
from app.services.telegram import TelegramClient

logger = logging.getLogger(__name__)
STALE_RUNNING_TASK_SECONDS = 300


async def recover_unfinished_tasks(settings: Settings) -> None:
    stale_before = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        seconds=STALE_RUNNING_TASK_SECONDS
    )
    with SessionLocal() as db:
        tasks = list(
            db.scalars(
                select(GenerationTask)
                .where(
                    GenerationTask.status == TaskStatus.running,
                    GenerationTask.external_job_id.is_not(None),
                    GenerationTask.updated_at < stale_before,
                )
                .order_by(GenerationTask.created_at)
            )
        )

    for task in tasks:
        try:
            await recover_task(task.id, settings)
        except Exception:
            logger.exception("Failed to recover task %s.", task.id)


async def recover_task(task_id: int, settings: Settings) -> None:
    comfyui = ComfyUIClient(settings)
    telegram = TelegramClient(settings)

    with SessionLocal() as db:
        task = db.get(GenerationTask, task_id)
        if not task or task.status != TaskStatus.running or not task.external_job_id:
            return
        prompt_id = task.external_job_id
        kind = task.kind
        chat_id = task.telegram_chat_id
        reply_to_message_id = task.telegram_message_id

    result_urls = await comfyui.get_result_urls(prompt_id)
    if result_urls:
        with SessionLocal() as db:
            task = _get_task(db, task_id)
            task.status = TaskStatus.completed
            task.result_urls = result_urls
            db.add(task)
            db.commit()
        if chat_id:
            await telegram.send_result_media(chat_id, result_urls, kind, reply_to_message_id)
        return

    await _retry_task(task_id, settings)


async def _retry_task(task_id: int, settings: Settings) -> None:
    comfyui = ComfyUIClient(settings)

    with SessionLocal() as db:
        task = _get_task(db, task_id)
        if task.status != TaskStatus.running or not task.workflow:
            return
        workflow = task.workflow
        prompt = task.interpreted_prompt or task.original_text or ""
        source_media_url = task.source_media_url
        chat_id = task.telegram_chat_id
        reply_to_message_id = task.telegram_message_id
        kind = task.kind

    async with concurrency_slot("comfyui", settings.comfyui_max_concurrency):
        try:
            new_prompt_id = await comfyui.submit_prompt(
                workflow=workflow,
                prompt=prompt,
                source_media_url=source_media_url,
            )
        except Exception as exc:
            detail = format_exception_details(exc)
            with SessionLocal() as db:
                task = _get_task(db, task_id)
                task.status = TaskStatus.failed
                task.error_message = detail
                if task.user:
                    refund_task_credits(db, task.user, task)
                db.add(task)
                db.commit()
            if chat_id:
                await TelegramClient(settings).send_message(
                    chat_id,
                    "未交付任务自动重试失败，积分已退回。请稍后重新提交。",
                    reply_to_message_id,
                )
            return

        with SessionLocal() as db:
            task = _get_task(db, task_id)
            task.external_job_id = new_prompt_id
            task.error_message = None
            db.add(task)
            db.commit()

        if chat_id:
            await TelegramClient(settings).send_message(
                chat_id,
                f"任务 #{task_id} 未交付，已自动重试，不会重复扣积分。",
                reply_to_message_id,
            )

        await complete_comfyui_task(
            task_id=task_id,
            prompt_id=new_prompt_id,
            chat_id=chat_id or "",
            reply_to_message_id=reply_to_message_id or "",
            kind=kind,
            settings=settings,
        )


def _get_task(db, task_id: int) -> GenerationTask:
    task = db.get(GenerationTask, task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found.")
    return task
