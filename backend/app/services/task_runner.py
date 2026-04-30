import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, replace
from typing import Any

import httpx
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
from app.services.telegram import (
    REGENERATE_CALLBACK_PREFIX,
    TelegramCallbackQuery,
    TelegramClient,
    TelegramUpdateError,
    build_regenerate_result_markup,
    build_result_caption,
)
from app.services.users import get_or_create_telegram_user

logger = logging.getLogger(__name__)

PENDING_SOURCE_MEDIA_URL = "telegram://pending-source-media"
FOLLOWUP_SOURCE_MEDIA_TTL_SECONDS = 120.0
FOLLOWUP_SOURCE_MEDIA_WAIT_SECONDS = 3.0


@dataclass
class _PendingSourceMedia:
    source_file_id: str
    source_media_type: str | None
    source_message_id: str
    created_at: float


@dataclass
class _PendingInstruction:
    target_output: TargetOutput
    created_at: float
    event: asyncio.Event
    source_media: _PendingSourceMedia | None = None


@dataclass
class _RecentInboundMessage:
    fingerprint: str
    message_id: str
    updated_at: float


_pending_followup_lock = asyncio.Lock()
_recent_source_media: dict[tuple[str, str], _PendingSourceMedia] = {}
_pending_instructions: dict[tuple[str, str], _PendingInstruction] = {}
_duplicate_message_lock = asyncio.Lock()
_recent_inbound_messages: dict[tuple[str, str], _RecentInboundMessage] = {}
_recent_generation_requests: dict[tuple[str, str], _RecentInboundMessage] = {}
_recent_regeneration_requests: dict[tuple[str, str, str], _RecentInboundMessage] = {}
_dedupe_redis_clients: dict[str, Any] = {}


async def process_telegram_update(update: dict, settings: Settings) -> None:
    request_started_at = time.perf_counter()
    telegram = TelegramClient(settings)
    welcome_chat_id = extract_welcome_chat_id(update)
    if welcome_chat_id:
        await telegram.send_message(
            welcome_chat_id,
            _render_help_message(include_welcome=True),
        )
        return

    try:
        callback_query = telegram.parse_callback_query(update)
    except TelegramUpdateError:
        return
    if callback_query:
        await _process_regenerate_callback(callback_query, settings, telegram)
        return

    try:
        inbound = telegram.parse_message(update)
    except TelegramUpdateError:
        return

    if await _is_consecutive_duplicate_message(inbound, settings):
        logger.info(
            "telegram_duplicate_message_skipped chat_id=%s telegram_id=%s message_id=%s",
            inbound.chat_id,
            inbound.telegram_id,
            inbound.message_id,
        )
        return

    command_arguments = extract_bot_command_arguments(inbound.text)

    if is_help_request(inbound.text):
        await telegram.send_message(
            inbound.chat_id,
            _render_help_message(include_welcome=is_start_command(inbound.text)),
            inbound.message_id,
        )
        return

    if inbound.source_file_id and not inbound.text:
        if await _attach_source_media_to_pending_instruction(inbound):
            return
        await _remember_recent_source_media(inbound)
        await telegram.send_message(
            inbound.chat_id,
            "已收到图片。请继续发送 /photo <描述>、/video <描述>，或直接发送“生图/改图/图生视频 + 要求”。",
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
        if quick_action == BotQuickAction.status:
            await telegram.send_message(
                inbound.chat_id,
                await _build_status_message(settings),
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

    if effective_text and not inbound.source_file_id:
        inbound = await _resolve_followup_source_media(
            inbound,
            explicit_target,
            wait_seconds=FOLLOWUP_SOURCE_MEDIA_WAIT_SECONDS,
        )

    if _image_request_needs_source_media(explicit_target, effective_text) and not inbound.source_file_id:
        await telegram.send_message(
            inbound.chat_id,
            "这看起来是图片编辑请求。请发送或转发要编辑的图片，并把要求作为图片配文，或回复那张图片发送要求。",
            inbound.message_id,
        )
        return

    if await _is_duplicate_generation_request(inbound, effective_text, explicit_target, settings):
        logger.info(
            "telegram_duplicate_generation_request_skipped chat_id=%s telegram_id=%s message_id=%s",
            inbound.chat_id,
            inbound.telegram_id,
            inbound.message_id,
        )
        await telegram.send_message(
            inbound.chat_id,
            "这条生成请求刚刚处理过，已跳过重复提交。",
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
        stage_started_at = time.perf_counter()
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
        logger.info(
            "telegram_preflight_complete chat_id=%s message_id=%s target=%s source_media=%s elapsed=%.2fs total=%.2fs",
            inbound.chat_id,
            inbound.message_id,
            preflight.target_output.value,
            preflight.source_media_type or "none",
            time.perf_counter() - stage_started_at,
            time.perf_counter() - request_started_at,
        )

    await telegram.send_message(
        inbound.chat_id,
        _preflight_acceptance_message(preflight),
        inbound.message_id,
    )

    source_media_url = None
    if inbound.source_file_id:
        stage_started_at = time.perf_counter()
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
        logger.info(
            "telegram_source_media_url_complete chat_id=%s message_id=%s elapsed=%.2fs total=%.2fs",
            inbound.chat_id,
            inbound.message_id,
            time.perf_counter() - stage_started_at,
            time.perf_counter() - request_started_at,
        )

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

    with SessionLocal() as db:
        stage_started_at = time.perf_counter()
        try:
            task = await orchestrator.enqueue_bot_message(db, payload)
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

        logger.info(
            "telegram_task_create_complete chat_id=%s message_id=%s task_id=%s status=%s elapsed=%.2fs total=%.2fs",
            inbound.chat_id,
            inbound.message_id,
            task.id,
            task.status.value,
            time.perf_counter() - stage_started_at,
            time.perf_counter() - request_started_at,
        )

        status = task.status
        created_task_id = task.id
        kind = task.kind
        credit_cost = task.credit_cost
        error_message = task.error_message

    if status == TaskStatus.failed:
        await telegram.send_message(
            inbound.chat_id,
            append_error_detail(
                "任务创建失败，积分已退回。请检查工作流配置。",
                error_message,
                label="详细信息",
                task_id=created_task_id,
            ),
            inbound.message_id,
        )
        return

    await telegram.send_message(
        inbound.chat_id,
        f"{_task_kind_label(kind)}已加入队列，任务 #{created_task_id}，实际消耗 {credit_cost} 积分。完成后会自动回传结果。",
        inbound.message_id,
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
        include_prompt = bool(task.user and task.user.is_admin)
        result_caption = build_result_caption(
            task.interpreted_prompt or task.original_text,
            task_id=task_id,
            include_prompt=include_prompt,
        )
        db.add(task)
        db.commit()

    try:
        await telegram.send_result_media(
            chat_id,
            result_urls,
            kind,
            reply_to_message_id,
            caption=result_caption,
            reply_markup=build_regenerate_result_markup(task_id),
        )
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


async def _process_regenerate_callback(
    callback_query: TelegramCallbackQuery,
    settings: Settings,
    telegram: TelegramClient,
) -> None:
    if not callback_query.data.startswith(REGENERATE_CALLBACK_PREFIX):
        await telegram.answer_callback_query(callback_query.callback_query_id)
        return

    raw_task_id = callback_query.data.removeprefix(REGENERATE_CALLBACK_PREFIX)
    try:
        source_task_id = int(raw_task_id)
    except ValueError:
        await telegram.answer_callback_query(callback_query.callback_query_id, "无效的任务。")
        return

    if await _is_duplicate_regeneration_request(callback_query, source_task_id, settings):
        logger.info(
            "telegram_duplicate_regeneration_skipped chat_id=%s telegram_id=%s source_task_id=%s",
            callback_query.chat_id,
            callback_query.telegram_id,
            source_task_id,
        )
        await telegram.answer_callback_query(
            callback_query.callback_query_id,
            "这条任务刚刚已重新生成，请稍后再试。",
        )
        return

    orchestrator = GenerationOrchestrator(settings)
    with SessionLocal() as db:
        source_task = db.get(GenerationTask, source_task_id)
        if not source_task:
            await telegram.answer_callback_query(callback_query.callback_query_id, "任务不存在。")
            return
        if not source_task.user or source_task.user.telegram_id != callback_query.telegram_id:
            await telegram.answer_callback_query(callback_query.callback_query_id, "只能重新生成自己的任务。")
            return

        try:
            task = orchestrator.enqueue_regeneration(db, source_task_id)
        except InsufficientCreditsError as exc:
            await telegram.answer_callback_query(callback_query.callback_query_id, str(exc))
            return
        except WorkflowUnavailableError as exc:
            await telegram.answer_callback_query(callback_query.callback_query_id, str(exc))
            return
        except ValueError as exc:
            await telegram.answer_callback_query(callback_query.callback_query_id, str(exc))
            return
        except Exception as exc:
            logger.exception("telegram_regenerate_callback_failed source_task_id=%s", source_task_id)
            await telegram.answer_callback_query(callback_query.callback_query_id, "重新生成失败。")
            await telegram.send_message(
                callback_query.chat_id,
                append_error_detail("重新生成失败。", format_exception_details(exc), label="详细信息"),
                callback_query.message_id,
            )
            return

        task_id = task.id
        kind = task.kind
        credit_cost = task.credit_cost

    await telegram.answer_callback_query(callback_query.callback_query_id, "已重新加入队列。")
    await telegram.send_message(
        callback_query.chat_id,
        f"{_task_kind_label(kind)}已重新加入队列，任务 #{task_id}，实际消耗 {credit_cost} 积分。完成后会自动回传结果。",
        callback_query.message_id,
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
    prefix_matches: list[tuple[int, TargetOutput, str | None]] = []
    for target_output, prefixes in (
        (TargetOutput.image, _IMAGE_REQUEST_PREFIXES),
        (TargetOutput.video, _VIDEO_REQUEST_PREFIXES),
    ):
        for prefix in prefixes:
            remainder = _strip_prefix(normalized, lowered, prefix)
            if remainder is not None:
                prefix_matches.append((len(prefix), target_output, remainder or None))

    if prefix_matches:
        _prefix_length, target_output, remainder = max(prefix_matches, key=lambda match: match[0])
        return target_output, remainder
    return None


_IMAGE_REQUEST_PREFIXES = (
    "生成图片",
    "生成圖片",
    "生成照片",
    "生图",
    "生圖",
    "出图",
    "出圖",
    "改图",
    "改圖",
    "改照片",
    "修图",
    "修圖",
    "图片编辑",
    "圖片編輯",
    "编辑图片",
    "編輯圖片",
    "编辑图",
    "編輯圖",
    "图片",
    "圖片",
    "照片",
    "p图",
    "p圖",
    "generate image",
    "generate photo",
    "generate picture",
    "create image",
    "create photo",
    "create picture",
    "make image",
    "make photo",
    "make picture",
    "text to image",
    "txt2img",
    "t2i",
    "edit image",
    "edit photo",
    "edit picture",
    "image edit",
    "photo edit",
    "picture edit",
    "modify image",
    "modify photo",
    "modify picture",
    "retouch image",
    "retouch photo",
    "retouch picture",
    "fix image",
    "fix photo",
    "fix picture",
    "enhance image",
    "enhance photo",
    "enhance picture",
    "image",
    "photo",
    "picture",
    "edit",
    "retouch",
)
_VIDEO_REQUEST_PREFIXES = (
    "生成视频",
    "生视频",
    "出视频",
    "文生视频",
    "图生视频",
    "视频",
    "generate video",
    "create video",
    "make video",
    "text to video",
    "image to video",
    "photo to video",
    "picture to video",
    "animate image",
    "animate photo",
    "animate picture",
    "txt2video",
    "txt2vid",
    "img2video",
    "img2vid",
    "t2v",
    "i2v",
    "video",
)
_PREFIX_SEPARATORS = (" ", "，", ",", "。", "：", ":", "-", "—", "_")
_SOURCE_MEDIA_EDIT_HINTS = (
    "edit",
    "change",
    "replace",
    "restyle",
    "modify",
    "retouch",
    "fix",
    "enhance",
    "outfit",
    "clothing",
    "dress",
    "background",
    "修改",
    "编辑",
    "改成",
    "变成",
    "替换",
    "换",
    "换个",
    "换掉",
    "脱",
    "去掉",
    "去除",
    "删除",
    "移除",
    "增加",
    "添加",
    "背景",
    "衣服",
    "服装",
    "皮肤",
    "p图",
    "美化",
)


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


def _image_request_needs_source_media(
    target_output: TargetOutput,
    effective_text: str | None,
) -> bool:
    if target_output != TargetOutput.image:
        return False
    normalized = _normalize_generation_text(effective_text)
    if not normalized:
        return False
    return any(token in normalized for token in _SOURCE_MEDIA_EDIT_HINTS)


def _normalize_generation_text(text: str | None) -> str:
    return " ".join((text or "").strip().lower().split())


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


async def _build_status_message(settings: Settings) -> str:
    lines = [
        "系统状态：",
        "Bot：在线",
        f"ComfyUI：{await _http_status_label(f'{settings.comfyui_base_url}/system_stats')}",
    ]
    if _llm_uses_ollama(settings):
        lines.append(f"Ollama：{await _http_status_label(f'{settings.ollama_base_url}/api/tags')}")
    if _llm_uses_minimax(settings):
        lines.append(f"MiniMax：{'已配置' if settings.minimax_api_key else '未配置'}")
    lines.append("")
    lines.append("能收到这条回复，表示 Telegram 轮询和消息处理在线。")
    return "\n".join(lines)


async def _http_status_label(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            response = await client.get(url)
            response.raise_for_status()
        return "在线"
    except Exception:
        return "离线"


def _llm_uses_ollama(settings: Settings) -> bool:
    return (
        settings.llm_logic_provider_name == "ollama"
        or settings.llm_prompt_provider_name == "ollama"
        or settings.llm_vision_provider_name == "ollama"
    )


def _llm_uses_minimax(settings: Settings) -> bool:
    return (
        settings.llm_logic_provider_name == "minimax"
        or settings.llm_prompt_provider_name == "minimax"
        or settings.llm_vision_provider_name == "minimax_mcp"
    )


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


async def _attach_source_media_to_pending_instruction(inbound) -> bool:
    if not inbound.source_file_id:
        return False

    key = _followup_key(inbound)
    async with _pending_followup_lock:
        _prune_pending_followup_state()
        pending = _pending_instructions.get(key)
        if not pending or not _is_source_media_compatible(
            pending.target_output,
            inbound.source_media_type,
        ):
            return False

        pending.source_media = _build_pending_source_media(inbound)
        pending.event.set()
        return True


async def _remember_recent_source_media(inbound) -> None:
    if not inbound.source_file_id:
        return

    key = _followup_key(inbound)
    async with _pending_followup_lock:
        _prune_pending_followup_state()
        _recent_source_media[key] = _build_pending_source_media(inbound)


async def _resolve_followup_source_media(
    inbound,
    target_output: TargetOutput,
    *,
    wait_seconds: float,
):
    media = await _pop_recent_source_media(inbound, target_output)
    if media:
        return _with_source_media(inbound, media)

    if wait_seconds <= 0:
        return inbound

    pending = await _register_pending_instruction(inbound, target_output)
    try:
        await asyncio.wait_for(pending.event.wait(), timeout=wait_seconds)
    except TimeoutError:
        pass
    finally:
        await _remove_pending_instruction(inbound, pending)

    if pending.source_media:
        return _with_source_media(inbound, pending.source_media)
    return inbound


async def _pop_recent_source_media(inbound, target_output: TargetOutput) -> _PendingSourceMedia | None:
    key = _followup_key(inbound)
    async with _pending_followup_lock:
        _prune_pending_followup_state()
        media = _recent_source_media.get(key)
        if not media or not _is_source_media_compatible(target_output, media.source_media_type):
            return None
        return _recent_source_media.pop(key)


async def _register_pending_instruction(inbound, target_output: TargetOutput) -> _PendingInstruction:
    key = _followup_key(inbound)
    pending = _PendingInstruction(
        target_output=target_output,
        created_at=time.monotonic(),
        event=asyncio.Event(),
    )
    async with _pending_followup_lock:
        _prune_pending_followup_state()
        _pending_instructions[key] = pending
    return pending


async def _remove_pending_instruction(inbound, pending: _PendingInstruction) -> None:
    key = _followup_key(inbound)
    async with _pending_followup_lock:
        if _pending_instructions.get(key) is pending:
            _pending_instructions.pop(key, None)


def _build_pending_source_media(inbound) -> _PendingSourceMedia:
    return _PendingSourceMedia(
        source_file_id=inbound.source_file_id,
        source_media_type=inbound.source_media_type,
        source_message_id=inbound.message_id,
        created_at=time.monotonic(),
    )


def _with_source_media(inbound, media: _PendingSourceMedia):
    return replace(
        inbound,
        source_file_id=media.source_file_id,
        source_media_type=media.source_media_type,
    )


def _followup_key(inbound) -> tuple[str, str]:
    return inbound.chat_id, inbound.telegram_id


def _is_source_media_compatible(
    target_output: TargetOutput,
    source_media_type: str | None,
) -> bool:
    if not source_media_type:
        return False
    if target_output == TargetOutput.image:
        return source_media_type == "image"
    if target_output == TargetOutput.video:
        return source_media_type in {"image", "video"}
    return False


def _prune_pending_followup_state() -> None:
    cutoff = time.monotonic() - FOLLOWUP_SOURCE_MEDIA_TTL_SECONDS
    for key, media in list(_recent_source_media.items()):
        if media.created_at < cutoff:
            _recent_source_media.pop(key, None)
    for key, pending in list(_pending_instructions.items()):
        if pending.created_at < cutoff:
            _pending_instructions.pop(key, None)


async def _clear_pending_followup_state_for_tests() -> None:
    async with _pending_followup_lock:
        _recent_source_media.clear()
        _pending_instructions.clear()
    async with _duplicate_message_lock:
        _recent_inbound_messages.clear()
        _recent_generation_requests.clear()
        _recent_regeneration_requests.clear()


async def _is_consecutive_duplicate_message(
    inbound,
    settings: Settings,
) -> bool:
    fingerprint = _inbound_message_fingerprint(inbound)
    if not fingerprint:
        return False
    if settings.telegram_update_queue_url:
        try:
            return await _is_redis_consecutive_duplicate_message(
                inbound,
                fingerprint,
                settings,
            )
        except Exception:
            logger.exception("Redis Telegram duplicate check failed; falling back to memory.")

    now = time.monotonic()
    window_seconds = max(0, settings.telegram_duplicate_message_window_seconds)
    key = _followup_key(inbound)
    async with _duplicate_message_lock:
        cutoff = now - window_seconds
        for recent_key, recent in list(_recent_inbound_messages.items()):
            if recent.updated_at < cutoff:
                _recent_inbound_messages.pop(recent_key, None)

        recent = _recent_inbound_messages.get(key)
        is_duplicate = (
            recent is not None
            and recent.fingerprint == fingerprint
            and recent.message_id != inbound.message_id
            and now - recent.updated_at <= window_seconds
        )
        _recent_inbound_messages[key] = _RecentInboundMessage(
            fingerprint=fingerprint,
            message_id=recent.message_id if is_duplicate and recent else inbound.message_id,
            updated_at=now,
        )
        return is_duplicate


async def _is_redis_consecutive_duplicate_message(
    inbound,
    fingerprint: str,
    settings: Settings,
) -> bool:
    window_seconds = max(1, settings.telegram_duplicate_message_window_seconds)
    redis = await _dedupe_redis_client(settings.telegram_update_queue_url)
    key_hash = hashlib.sha256(f"{inbound.chat_id}\0{inbound.telegram_id}".encode()).hexdigest()
    key = f"{settings.telegram_update_queue_stream}:dedupe:{key_hash}"
    result = await redis.eval(
        """
local current = redis.call('GET', KEYS[1])
local is_duplicate = 0
if current then
  local separator = string.find(current, '\\n', 1, true)
  local current_fingerprint = current
  local current_message_id = ''
  if separator then
    current_fingerprint = string.sub(current, 1, separator - 1)
    current_message_id = string.sub(current, separator + 1)
  end
  if current_fingerprint == ARGV[1] and current_message_id ~= ARGV[2] then
    is_duplicate = 1
  end
end
if is_duplicate == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
else
  redis.call('SET', KEYS[1], ARGV[1] .. '\\n' .. ARGV[2], 'EX', tonumber(ARGV[3]))
end
return is_duplicate
        """,
        1,
        key,
        fingerprint,
        inbound.message_id,
        window_seconds,
    )
    return int(result) == 1


async def _dedupe_redis_client(redis_url: str) -> Any:
    client = _dedupe_redis_clients.get(redis_url)
    if client is not None:
        return client

    from redis import asyncio as redis

    client = redis.from_url(redis_url, decode_responses=True)
    _dedupe_redis_clients[redis_url] = client
    return client


async def _is_duplicate_generation_request(
    inbound,
    effective_text: str | None,
    target_output: TargetOutput,
    settings: Settings,
) -> bool:
    fingerprint = _generation_request_fingerprint(inbound, effective_text, target_output)
    if not fingerprint:
        return False
    if settings.telegram_update_queue_url:
        try:
            return await _is_redis_duplicate_generation_request(inbound, fingerprint, settings)
        except Exception:
            logger.exception("Redis Telegram generation duplicate check failed; falling back to memory.")

    now = time.monotonic()
    window_seconds = max(0, settings.telegram_duplicate_message_window_seconds)
    key = _followup_key(inbound)
    async with _duplicate_message_lock:
        cutoff = now - window_seconds
        for recent_key, recent in list(_recent_generation_requests.items()):
            if recent.updated_at < cutoff:
                _recent_generation_requests.pop(recent_key, None)

        recent = _recent_generation_requests.get(key)
        is_duplicate = (
            recent is not None
            and recent.fingerprint == fingerprint
            and recent.message_id != inbound.message_id
            and now - recent.updated_at <= window_seconds
        )
        _recent_generation_requests[key] = _RecentInboundMessage(
            fingerprint=fingerprint,
            message_id=recent.message_id if is_duplicate and recent else inbound.message_id,
            updated_at=now,
        )
        return is_duplicate


async def _is_redis_duplicate_generation_request(
    inbound,
    fingerprint: str,
    settings: Settings,
) -> bool:
    window_seconds = max(1, settings.telegram_duplicate_message_window_seconds)
    redis = await _dedupe_redis_client(settings.telegram_update_queue_url)
    key_hash = hashlib.sha256(
        f"{inbound.chat_id}\0{inbound.telegram_id}\0generation".encode()
    ).hexdigest()
    key = f"{settings.telegram_update_queue_stream}:generation-dedupe:{key_hash}"
    result = await redis.eval(
        """
local current = redis.call('GET', KEYS[1])
local is_duplicate = 0
if current then
  local separator = string.find(current, '\\n', 1, true)
  local current_fingerprint = current
  local current_message_id = ''
  if separator then
    current_fingerprint = string.sub(current, 1, separator - 1)
    current_message_id = string.sub(current, separator + 1)
  end
  if current_fingerprint == ARGV[1] and current_message_id ~= ARGV[2] then
    is_duplicate = 1
  end
end
if is_duplicate == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
else
  redis.call('SET', KEYS[1], ARGV[1] .. '\\n' .. ARGV[2], 'EX', tonumber(ARGV[3]))
end
return is_duplicate
        """,
        1,
        key,
        fingerprint,
        inbound.message_id,
        window_seconds,
    )
    return int(result) == 1


async def _is_duplicate_regeneration_request(
    callback_query: TelegramCallbackQuery,
    source_task_id: int,
    settings: Settings,
) -> bool:
    cooldown_seconds = max(0, settings.telegram_regenerate_cooldown_seconds)
    if cooldown_seconds <= 0:
        return False
    fingerprint = f"regenerate:{source_task_id}"
    if settings.telegram_update_queue_url:
        try:
            return await _is_redis_duplicate_regeneration_request(
                callback_query,
                source_task_id,
                fingerprint,
                settings,
            )
        except Exception:
            logger.exception("Redis Telegram regeneration duplicate check failed; falling back to memory.")

    now = time.monotonic()
    key = _regeneration_key(callback_query, source_task_id)
    async with _duplicate_message_lock:
        cutoff = now - cooldown_seconds
        for recent_key, recent in list(_recent_regeneration_requests.items()):
            if recent.updated_at < cutoff:
                _recent_regeneration_requests.pop(recent_key, None)

        recent = _recent_regeneration_requests.get(key)
        is_duplicate = (
            recent is not None
            and recent.fingerprint == fingerprint
            and now - recent.updated_at <= cooldown_seconds
        )
        _recent_regeneration_requests[key] = _RecentInboundMessage(
            fingerprint=fingerprint,
            message_id=callback_query.callback_query_id,
            updated_at=now,
        )
        return is_duplicate


async def _is_redis_duplicate_regeneration_request(
    callback_query: TelegramCallbackQuery,
    source_task_id: int,
    fingerprint: str,
    settings: Settings,
) -> bool:
    cooldown_seconds = max(1, settings.telegram_regenerate_cooldown_seconds)
    redis = await _dedupe_redis_client(settings.telegram_update_queue_url)
    key_hash = hashlib.sha256(
        (
            f"{callback_query.chat_id}\0{callback_query.telegram_id}"
            f"\0regenerate\0{source_task_id}"
        ).encode()
    ).hexdigest()
    key = f"{settings.telegram_update_queue_stream}:regenerate-dedupe:{key_hash}"
    result = await redis.eval(
        """
local existed = redis.call('EXISTS', KEYS[1])
if existed == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
  return 1
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]))
return 0
        """,
        1,
        key,
        fingerprint,
        cooldown_seconds,
    )
    return int(result) == 1


def _regeneration_key(
    callback_query: TelegramCallbackQuery,
    source_task_id: int,
) -> tuple[str, str, str]:
    return callback_query.chat_id, callback_query.telegram_id, str(source_task_id)


def _generation_request_fingerprint(
    inbound,
    effective_text: str | None,
    target_output: TargetOutput,
) -> str | None:
    normalized_text = _normalize_generation_text(effective_text)
    source_media = inbound.source_file_id or ""
    source_media_type = inbound.source_media_type or ""
    if not normalized_text and not source_media:
        return None
    raw_fingerprint = "\n".join(
        [
            f"target_output={target_output.value}",
            f"text={normalized_text}",
            f"source_media_type={source_media_type}",
            f"source_file_id={source_media}",
        ]
    )
    return hashlib.sha256(raw_fingerprint.encode()).hexdigest()


def _inbound_message_fingerprint(inbound) -> str | None:
    normalized_text = _normalize_generation_text(inbound.text)
    source_media = inbound.source_file_id or ""
    source_media_type = inbound.source_media_type or ""
    if not normalized_text and not source_media:
        return None
    raw_fingerprint = "\n".join(
        [
            f"text={normalized_text}",
            f"source_media_type={source_media_type}",
            f"source_file_id={source_media}",
        ]
    )
    return hashlib.sha256(raw_fingerprint.encode()).hexdigest()
