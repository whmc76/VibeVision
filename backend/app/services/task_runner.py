from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import SessionLocal
from app.models import GenerationTask, LedgerReason, TaskStatus
from app.schemas import BotMessageRequest
from app.services.comfyui import ComfyUIClient
from app.services.credits import adjust_credits
from app.services.error_details import append_error_detail, format_exception_details
from app.services.orchestrator import GenerationOrchestrator, WorkflowUnavailableError
from app.services.telegram import TelegramClient, TelegramUpdateError


async def process_telegram_update(update: dict, settings: Settings) -> None:
    telegram = TelegramClient(settings)
    try:
        inbound = telegram.parse_message(update)
    except TelegramUpdateError:
        return

    if not inbound.text and not inbound.source_file_id:
        await telegram.send_message(
            inbound.chat_id,
            "请发送文字描述，或发送图片并附上要生成、编辑或转视频的要求。",
            inbound.message_id,
        )
        return

    source_media_url = None
    if inbound.source_file_id:
        source_media_url = await telegram.get_file_url(inbound.source_file_id)

    await telegram.send_message(
        inbound.chat_id,
        "已收到请求，正在理解意图并提交生成任务。",
        inbound.message_id,
    )

    payload = BotMessageRequest(
        telegram_id=inbound.telegram_id,
        username=inbound.username,
        display_name=inbound.display_name,
        text=inbound.text,
        source_media_url=source_media_url,
        telegram_chat_id=inbound.chat_id,
        telegram_message_id=inbound.message_id,
    )

    with SessionLocal() as db:
        try:
            task = await GenerationOrchestrator(settings).handle_bot_message(db, payload)
        except WorkflowUnavailableError as exc:
            await telegram.send_message(
                inbound.chat_id,
                append_error_detail("没有可用工作流。", str(exc), label="详细信息"),
                inbound.message_id,
            )
            return
        except ValueError as exc:
            await telegram.send_message(
                inbound.chat_id,
                append_error_detail("请求处理失败。", str(exc), label="详细信息"),
                inbound.message_id,
            )
            return
        except Exception as exc:
            await telegram.send_message(
                inbound.chat_id,
                append_error_detail(
                    "任务创建失败，请检查后端配置和工作流参数。",
                    format_exception_details(exc),
                    label="详细信息",
                ),
                inbound.message_id,
            )
            return

        task_id = task.id
        status = task.status
        external_job_id = task.external_job_id
        kind = task.kind
        credit_cost = task.credit_cost
        error_message = task.error_message

    if status == TaskStatus.failed or not external_job_id:
        await telegram.send_message(
            inbound.chat_id,
            append_error_detail(
                "任务创建失败，积分已退回。请检查 ComfyUI 服务和工作流参数。",
                error_message,
                label="详细信息",
            ),
            inbound.message_id,
        )
        return

    await telegram.send_message(
        inbound.chat_id,
        f"任务 #{task_id} 已提交，预计消耗 {credit_cost} 积分。完成后会自动回传结果。",
        inbound.message_id,
    )

    await complete_comfyui_task(
        task_id=task_id,
        prompt_id=external_job_id,
        chat_id=inbound.chat_id,
        reply_to_message_id=inbound.message_id,
        kind=kind,
        settings=settings,
    )


async def complete_comfyui_task(
    task_id: int,
    prompt_id: str,
    chat_id: str,
    reply_to_message_id: str,
    kind,
    settings: Settings,
) -> None:
    telegram = TelegramClient(settings)
    comfyui = ComfyUIClient(settings)

    try:
        result_urls = await comfyui.wait_for_result(prompt_id)
    except Exception as exc:
        detail = format_exception_details(exc)
        with SessionLocal() as db:
            task = _get_task(db, task_id)
            task.status = TaskStatus.failed
            task.error_message = detail
            if task.user:
                adjust_credits(
                    db=db,
                    user=task.user,
                    amount=task.credit_cost,
                    reason=LedgerReason.task_refunded,
                    note="Refunded after ComfyUI result polling failed.",
                    task_id=task.id,
                )
            db.add(task)
            db.commit()
        await telegram.send_message(
            chat_id,
            append_error_detail(
                "生成任务失败，积分已退回。请检查 ComfyUI 返回结果和节点输出。",
                detail,
                label="详细信息",
            ),
            reply_to_message_id,
        )
        return

    with SessionLocal() as db:
        task = _get_task(db, task_id)
        task.status = TaskStatus.completed
        task.result_urls = result_urls
        db.add(task)
        db.commit()

    try:
        await telegram.send_result_media(chat_id, result_urls, kind, reply_to_message_id)
    except Exception as exc:
        await telegram.send_message(
            chat_id,
            append_error_detail(
                "任务已完成，但结果回传到 Telegram 失败。管理员可在后台查看任务输出。",
                format_exception_details(exc),
                label="详细信息",
            ),
            reply_to_message_id,
        )


def _get_task(db: Session, task_id: int) -> GenerationTask:
    task = db.get(GenerationTask, task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found.")
    return task
