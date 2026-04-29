from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import SessionLocal
from app.models import GenerationTask, TaskKind, TaskStatus, Workflow
from app.schemas import BotMessageRequest
from app.services.bot_help import (
    BotQuickAction,
    build_account_message,
    build_help_message,
    build_image_workflow_message,
    build_video_workflow_message,
    extract_bot_command,
    extract_bot_command_arguments,
    extract_welcome_chat_id,
    is_help_request,
    is_start_command,
    resolve_quick_action,
)
from app.services.comfyui import ComfyUIClient
from app.services.concurrency import concurrency_slot
from app.services.credits import InsufficientCreditsError, refresh_daily_bonus, refund_task_credits
from app.services.error_details import append_error_detail, format_exception_details
from app.services.gpu_memory import comfyui_gpu_scope
from app.services.intent import TargetOutput, TargetOutputRequiredError
from app.services.orchestrator import (
    GenerationOrchestrator,
    GenerationPreflight,
    WorkflowUnavailableError,
)
from app.services.telegram import TelegramClient, TelegramUpdateError
from app.services.users import get_or_create_telegram_user

PENDING_SOURCE_MEDIA_URL = "telegram://pending-source-media"


async def process_telegram_update(update: dict, settings: Settings) -> None:
    telegram = TelegramClient(settings)
    welcome_chat_id = extract_welcome_chat_id(update)
    if welcome_chat_id:
        await telegram.send_message(
            welcome_chat_id,
            _render_help_message(include_welcome=True),
        )
        return

    try:
        inbound = telegram.parse_message(update)
    except TelegramUpdateError:
        return

    command_arguments = extract_bot_command_arguments(inbound.text)

    if is_help_request(inbound.text):
        await telegram.send_message(
            inbound.chat_id,
            _render_help_message(include_welcome=is_start_command(inbound.text)),
            inbound.message_id,
        )
        return

    if not inbound.source_file_id:
        quick_action = resolve_quick_action(inbound.text)
        if quick_action == BotQuickAction.image and not command_arguments:
            await telegram.send_message(
                inbound.chat_id,
                _render_image_workflow_message(),
                inbound.message_id,
            )
            return
        if quick_action == BotQuickAction.video and not command_arguments:
            await telegram.send_message(
                inbound.chat_id,
                _render_video_workflow_message(),
                inbound.message_id,
            )
            return
        if quick_action == BotQuickAction.query:
            with SessionLocal() as db:
                user = get_or_create_telegram_user(
                    db,
                    telegram_id=inbound.telegram_id,
                    username=inbound.username,
                    display_name=inbound.display_name,
                )
                refresh_daily_bonus(db, user)
                db.commit()
                account_message = build_account_message(user)
            await telegram.send_message(
                inbound.chat_id,
                account_message,
                inbound.message_id,
            )
            return

    if not inbound.text and not inbound.source_file_id:
        await telegram.send_message(
            inbound.chat_id,
            "请发送文字描述，或发送图片并附上要生成、编辑或转视频的要求。\n发送 /start 查看支持的能力和示例。",
            inbound.message_id,
        )
        return

    generation_request = _parse_generation_request(inbound.text)
    if not generation_request:
        await telegram.send_message(
            inbound.chat_id,
            _render_help_message(include_welcome=False),
            inbound.message_id,
        )
        return

    explicit_target, effective_text = generation_request
    if not effective_text and not inbound.source_file_id:
        await telegram.send_message(
            inbound.chat_id,
            _render_image_workflow_message()
            if explicit_target == TargetOutput.image
            else _render_video_workflow_message(),
            inbound.message_id,
        )
        return

    preflight_payload = BotMessageRequest(
        telegram_id=inbound.telegram_id,
        username=inbound.username,
        display_name=inbound.display_name,
        text=effective_text,
        source_media_url=PENDING_SOURCE_MEDIA_URL if inbound.source_file_id else None,
        source_media_type=inbound.source_media_type,
        target_output=explicit_target.value if explicit_target else None,
        telegram_chat_id=inbound.chat_id,
        telegram_message_id=inbound.message_id,
    )
    orchestrator = GenerationOrchestrator(settings)

    with SessionLocal() as db:
        try:
            preflight = orchestrator.preflight_bot_message(db, preflight_payload)
        except WorkflowUnavailableError as exc:
            await telegram.send_message(
                inbound.chat_id,
                append_error_detail("没有可用工作流。", str(exc), label="详细信息"),
                inbound.message_id,
            )
            return
        except TargetOutputRequiredError as exc:
            await telegram.send_message(
                inbound.chat_id,
                str(exc),
                inbound.message_id,
            )
            return
        except InsufficientCreditsError as exc:
            await telegram.send_message(
                inbound.chat_id,
                str(exc),
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

    await telegram.send_message(
        inbound.chat_id,
        _preflight_acceptance_message(preflight),
        inbound.message_id,
    )

    source_media_url = None
    if inbound.source_file_id:
        try:
            source_media_url = await telegram.get_file_url(inbound.source_file_id)
        except Exception as exc:
            await telegram.send_message(
                inbound.chat_id,
                append_error_detail(
                    "已收到请求，但读取 Telegram 媒体失败，请重新发送图片或视频。",
                    format_exception_details(exc),
                    label="详细信息",
                ),
                inbound.message_id,
            )
            return

    payload = BotMessageRequest(
        telegram_id=inbound.telegram_id,
        username=inbound.username,
        display_name=inbound.display_name,
        text=effective_text,
        source_media_url=source_media_url,
        source_media_type=inbound.source_media_type,
        target_output=explicit_target.value if explicit_target else None,
        telegram_chat_id=inbound.chat_id,
        telegram_message_id=inbound.message_id,
    )

    async with comfyui_gpu_scope(settings):
        async with concurrency_slot("comfyui", settings.comfyui_max_concurrency):
            with SessionLocal() as db:
                try:
                    task = await orchestrator.handle_bot_message(db, payload)
                except WorkflowUnavailableError as exc:
                    await telegram.send_message(
                        inbound.chat_id,
                        append_error_detail("没有可用工作流。", str(exc), label="详细信息"),
                        inbound.message_id,
                    )
                    return
                except TargetOutputRequiredError as exc:
                    await telegram.send_message(
                        inbound.chat_id,
                        str(exc),
                        inbound.message_id,
                    )
                    return
                except InsufficientCreditsError as exc:
                    await telegram.send_message(
                        inbound.chat_id,
                        str(exc),
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

                status = task.status
                external_job_id = task.external_job_id
                kind = task.kind
                credit_cost = task.credit_cost
                error_message = task.error_message

            if status == TaskStatus.failed or not external_job_id:
                await telegram.send_message(
                    inbound.chat_id,
                    append_error_detail(
                        "任务创建失败，积分已退回。请检查 ComfyUI 工作流配置。",
                        error_message,
                        label="详细信息",
                        task_id=task.id,
                    ),
                    inbound.message_id,
                )
                return

            await telegram.send_message(
                inbound.chat_id,
                f"{_task_kind_label(kind)}已提交，实际消耗 {credit_cost} 积分。完成后会自动回传结果。",
                inbound.message_id,
            )

            await complete_comfyui_task(
                task_id=task.id,
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
    async with comfyui_gpu_scope(settings):
        async with concurrency_slot("comfyui", settings.comfyui_max_concurrency):
            await _complete_comfyui_task_locked(
                task_id=task_id,
                prompt_id=prompt_id,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                kind=kind,
                settings=settings,
            )


async def _complete_comfyui_task_locked(
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
        late_result_urls = await _get_late_result_urls(comfyui, prompt_id)
        if late_result_urls:
            await _complete_task_success(
                task_id=task_id,
                result_urls=late_result_urls,
                telegram=telegram,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                kind=kind,
            )
            return

        detail = format_exception_details(exc)
        with SessionLocal() as db:
            task = _get_task(db, task_id)
            task.status = TaskStatus.failed
            task.error_message = detail
            if task.user:
                refund_task_credits(db, task.user, task)
            db.add(task)
            db.commit()
        await telegram.send_message(
            chat_id,
            append_error_detail(
                "生成任务失败，积分已退回。请检查 ComfyUI 返回结果和节点输出。",
                detail,
                label="详细信息",
                task_id=task_id,
            ),
            reply_to_message_id,
        )
        return

    await _complete_task_success(
        task_id=task_id,
        result_urls=result_urls,
        telegram=telegram,
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        kind=kind,
    )


async def _get_late_result_urls(comfyui: ComfyUIClient, prompt_id: str) -> list[str]:
    try:
        return await comfyui.get_result_urls(prompt_id)
    except Exception:
        return []


async def _complete_task_success(
    *,
    task_id: int,
    result_urls: list[str],
    telegram: TelegramClient,
    chat_id: str,
    reply_to_message_id: str,
    kind,
) -> None:
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
                task_id=task_id,
            ),
            reply_to_message_id,
        )


def _get_task(db: Session, task_id: int) -> GenerationTask:
    task = db.get(GenerationTask, task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found.")
    return task


def _render_help_message(*, include_welcome: bool) -> str:
    return build_help_message(_load_active_workflows(), include_welcome=include_welcome)


def _render_image_workflow_message() -> str:
    return build_image_workflow_message(_load_active_workflows())


def _render_video_workflow_message() -> str:
    return build_video_workflow_message(_load_active_workflows())


def _target_output_from_command(text: str | None) -> TargetOutput | None:
    command = extract_bot_command(text)
    if command in {"/photo", "/p", "/image", "/photos", "/images"}:
        return TargetOutput.image
    if command in {"/video", "/v", "/videos"}:
        return TargetOutput.video
    return None


def _parse_generation_request(text: str | None) -> tuple[TargetOutput, str | None] | None:
    command_target = _target_output_from_command(text)
    if command_target:
        return command_target, extract_bot_command_arguments(text)

    normalized = " ".join((text or "").strip().split())
    if not normalized:
        return None

    lowered = normalized.lower()
    for prefix in _IMAGE_REQUEST_PREFIXES:
        remainder = _strip_prefix(normalized, lowered, prefix)
        if remainder is not None:
            return TargetOutput.image, remainder or None
    for prefix in _VIDEO_REQUEST_PREFIXES:
        remainder = _strip_prefix(normalized, lowered, prefix)
        if remainder is not None:
            return TargetOutput.video, remainder or None
    return None


_IMAGE_REQUEST_PREFIXES = (
    "生成图片",
    "生成照片",
    "生图",
    "出图",
    "图片",
    "照片",
    "改图",
    "改照片",
    "修图",
    "p图",
    "image",
    "photo",
    "picture",
)
_VIDEO_REQUEST_PREFIXES = (
    "生成视频",
    "生视频",
    "出视频",
    "文生视频",
    "图生视频",
    "视频",
    "video",
)
_PREFIX_SEPARATORS = (" ", "，", ",", "。", "：", ":", "-", "—", "_")


def _strip_prefix(original: str, lowered: str, prefix: str) -> str | None:
    prefix_lowered = prefix.lower()
    if lowered == prefix_lowered:
        return ""
    if not lowered.startswith(prefix_lowered):
        return None

    remainder = original[len(prefix) :].strip()
    if remainder.startswith(_PREFIX_SEPARATORS):
        remainder = remainder[1:].strip()
    return remainder or None


def _preflight_acceptance_message(preflight: GenerationPreflight) -> str:
    target_label = "视频" if preflight.target_output == TargetOutput.video else "图片"
    return (
        f"已收到{target_label}请求，预计消耗 {preflight.credit_cost} 积分。"
        "正在理解内容并提交任务。"
    )


def _task_kind_label(kind: TaskKind) -> str:
    labels = {
        TaskKind.image_generate: "图像生成任务",
        TaskKind.image_edit: "图像编辑任务",
        TaskKind.video_text_to_video: "视频生成任务",
        TaskKind.video_image_to_video: "图生视频任务",
        TaskKind.prompt_expand: "提示词扩写任务",
    }
    return labels.get(kind, "生成任务")


def _load_active_workflows() -> list[Workflow]:
    try:
        with SessionLocal() as db:
            return list(
                db.scalars(
                    select(Workflow)
                    .where(Workflow.is_active.is_(True))
                    .order_by(Workflow.kind, Workflow.id)
                )
            )
    except Exception:
        return []
