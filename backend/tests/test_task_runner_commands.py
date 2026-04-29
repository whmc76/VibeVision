import asyncio

from app.core.config import Settings
from app.models import TaskKind
from app.services.intent import TargetOutput
from app.services.task_runner import (
    _attach_source_media_to_pending_instruction,
    _clear_pending_followup_state_for_tests,
    _is_consecutive_duplicate_message,
    _parse_generation_request,
    _remember_recent_source_media,
    _resolve_followup_source_media,
    _target_output_from_command,
    _task_kind_label,
)
from app.services.telegram import TelegramInboundMessage


def test_generation_commands_resolve_target_output() -> None:
    assert _target_output_from_command("/photo 生成一张海报") == TargetOutput.image
    assert _target_output_from_command("/p 生成一张海报") == TargetOutput.image
    assert _target_output_from_command("/video 生成一个短视频") == TargetOutput.video
    assert _target_output_from_command("/v 生成一个短视频") == TargetOutput.video
    assert _target_output_from_command("/check") is None


def test_generation_request_requires_known_keyword_or_command() -> None:
    assert _parse_generation_request("随便聊一句") is None


def test_generation_request_parses_image_prefixes() -> None:
    assert _parse_generation_request("/p 生成一张海报") == (
        TargetOutput.image,
        "生成一张海报",
    )
    assert _parse_generation_request("生图 一个赛博朋克女孩") == (
        TargetOutput.image,
        "一个赛博朋克女孩",
    )
    assert _parse_generation_request("改图，把背景换成海边") == (
        TargetOutput.image,
        "把背景换成海边",
    )


def test_generation_request_parses_prefix_only_as_target_selection() -> None:
    assert _parse_generation_request("生图") == (TargetOutput.image, None)
    assert _parse_generation_request("视频") == (TargetOutput.video, None)


def test_generation_request_parses_video_prefixes() -> None:
    assert _parse_generation_request("/v 生成一个短视频") == (
        TargetOutput.video,
        "生成一个短视频",
    )
    assert _parse_generation_request("图生视频 镜头缓慢推进") == (
        TargetOutput.video,
        "镜头缓慢推进",
    )


def test_task_kind_label_uses_user_facing_task_names() -> None:
    assert _task_kind_label(TaskKind.image_generate) == "图像生成任务"
    assert _task_kind_label(TaskKind.image_edit) == "图像编辑任务"
    assert _task_kind_label(TaskKind.video_text_to_video) == "视频生成任务"
    assert _task_kind_label(TaskKind.video_image_to_video) == "图生视频任务"


def test_followup_source_media_can_be_used_by_next_instruction() -> None:
    async def scenario() -> None:
        await _clear_pending_followup_state_for_tests()
        await _remember_recent_source_media(
            _inbound_message(message_id="10", source_file_id="photo-file")
        )

        resolved = await _resolve_followup_source_media(
            _inbound_message(message_id="11", text="生图 脱衣"),
            TargetOutput.image,
            wait_seconds=0,
        )

        assert resolved.source_file_id == "photo-file"
        assert resolved.source_media_type == "image"

    asyncio.run(scenario())


def test_followup_source_media_can_arrive_after_instruction() -> None:
    async def scenario() -> None:
        await _clear_pending_followup_state_for_tests()
        instruction = _inbound_message(message_id="10", text="生图 脱衣")
        resolver = asyncio.create_task(
            _resolve_followup_source_media(
                instruction,
                TargetOutput.image,
                wait_seconds=0.5,
            )
        )
        await asyncio.sleep(0)

        attached = await _attach_source_media_to_pending_instruction(
            _inbound_message(message_id="11", source_file_id="photo-file")
        )
        resolved = await resolver

        assert attached is True
        assert resolved.source_file_id == "photo-file"
        assert resolved.source_media_type == "image"

    asyncio.run(scenario())


def test_consecutive_duplicate_message_is_skipped() -> None:
    async def scenario() -> None:
        await _clear_pending_followup_state_for_tests()
        settings = Settings(telegram_duplicate_message_window_seconds=45)

        assert (
            await _is_consecutive_duplicate_message(
                _inbound_message(message_id="10", text="生图   赛博朋克女孩"),
                settings,
            )
            is False
        )
        assert (
            await _is_consecutive_duplicate_message(
                _inbound_message(message_id="11", text="生图 赛博朋克女孩"),
                settings,
            )
            is True
        )
        assert (
            await _is_consecutive_duplicate_message(
                _inbound_message(message_id="12", text="生图 赛博朋克城市"),
                settings,
            )
            is False
        )

    asyncio.run(scenario())


def test_same_message_id_redelivery_is_not_treated_as_anxious_repeat() -> None:
    async def scenario() -> None:
        await _clear_pending_followup_state_for_tests()
        settings = Settings(telegram_duplicate_message_window_seconds=45)
        inbound = _inbound_message(message_id="10", text="生图 赛博朋克女孩")

        assert await _is_consecutive_duplicate_message(inbound, settings) is False
        assert await _is_consecutive_duplicate_message(inbound, settings) is False

    asyncio.run(scenario())


def _inbound_message(
    *,
    message_id: str,
    text: str | None = None,
    source_file_id: str | None = None,
) -> TelegramInboundMessage:
    return TelegramInboundMessage(
        telegram_id="123",
        chat_id="456",
        message_id=message_id,
        username="alice",
        display_name="Alice",
        text=text,
        source_file_id=source_file_id,
        source_media_type="image" if source_file_id else None,
    )
