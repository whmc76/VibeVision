import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.db.session import Base
from app.models import GenerationTask, TaskKind, TaskStatus, User, Workflow
from app.services.intent import TargetOutput
from app.services.task_runner import (
    _attach_source_media_to_pending_instruction,
    _build_status_message,
    _clear_pending_followup_state_for_tests,
    _complete_task_success,
    _image_request_needs_source_media,
    _is_consecutive_duplicate_message,
    _is_duplicate_generation_request,
    _is_duplicate_regeneration_request,
    _parse_generation_request,
    _remember_recent_source_media,
    _resolve_followup_source_media,
    _target_output_from_command,
    _task_kind_label,
)
from app.services.telegram import TelegramCallbackQuery, TelegramInboundMessage


def test_generation_commands_resolve_target_output() -> None:
    assert _target_output_from_command("/photo 生成一张海报") == TargetOutput.image
    assert _target_output_from_command("/p 生成一张海报") == TargetOutput.image
    assert _target_output_from_command("/video 生成一个短视频") == TargetOutput.video
    assert _target_output_from_command("/v 生成一个短视频") == TargetOutput.video
    assert _target_output_from_command("/check") is None
    assert _target_output_from_command("/status") is None


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
    assert _parse_generation_request("生圖脱衣") == (
        TargetOutput.image,
        "脱衣",
    )
    assert _parse_generation_request("生成圖片 一张赛博朋克女孩") == (
        TargetOutput.image,
        "一张赛博朋克女孩",
    )
    assert _parse_generation_request("改图，把背景换成海边") == (
        TargetOutput.image,
        "把背景换成海边",
    )
    assert _parse_generation_request("修图，把皮肤修自然一点") == (
        TargetOutput.image,
        "把皮肤修自然一点",
    )
    assert _parse_generation_request("图片编辑 把背景换成海边") == (
        TargetOutput.image,
        "把背景换成海边",
    )
    assert _parse_generation_request("编辑图：加一副墨镜") == (
        TargetOutput.image,
        "加一副墨镜",
    )
    assert _parse_generation_request("编辑图片，加暖色电影感") == (
        TargetOutput.image,
        "加暖色电影感",
    )
    assert _parse_generation_request("generate image cyberpunk portrait") == (
        TargetOutput.image,
        "cyberpunk portrait",
    )
    assert _parse_generation_request("create photo cinematic headshot") == (
        TargetOutput.image,
        "cinematic headshot",
    )
    assert _parse_generation_request("text to image: a glass house in forest") == (
        TargetOutput.image,
        "a glass house in forest",
    )
    assert _parse_generation_request("edit image, change background to beach") == (
        TargetOutput.image,
        "change background to beach",
    )
    assert _parse_generation_request("retouch photo fix skin naturally") == (
        TargetOutput.image,
        "fix skin naturally",
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
    assert _parse_generation_request("generate video neon rainy street") == (
        TargetOutput.video,
        "neon rainy street",
    )
    assert _parse_generation_request("text to video: cinematic ocean sunset") == (
        TargetOutput.video,
        "cinematic ocean sunset",
    )
    assert _parse_generation_request("image to video slow push-in") == (
        TargetOutput.video,
        "slow push-in",
    )
    assert _parse_generation_request("animate photo, hair moving in wind") == (
        TargetOutput.video,
        "hair moving in wind",
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


def test_image_edit_like_request_requires_source_media() -> None:
    assert _image_request_needs_source_media(TargetOutput.image, "脱衣") is True
    assert _image_request_needs_source_media(TargetOutput.image, "把背景换成海边") is True
    assert _image_request_needs_source_media(TargetOutput.image, "一只赛博朋克猫") is False
    assert _image_request_needs_source_media(TargetOutput.video, "把背景换成海边") is False


def test_generation_duplicate_check_uses_effective_text_and_source_media() -> None:
    async def scenario() -> None:
        await _clear_pending_followup_state_for_tests()
        settings = Settings(telegram_duplicate_message_window_seconds=45)

        first = _inbound_message(
            message_id="10",
            text="生图 脱衣",
            source_file_id="photo-file",
        )
        second = _inbound_message(
            message_id="11",
            text="修图 脱衣",
            source_file_id="photo-file",
        )

        assert (
            await _is_duplicate_generation_request(
                first,
                "脱衣",
                TargetOutput.image,
                settings,
            )
            is False
        )
        assert (
            await _is_duplicate_generation_request(
                second,
                "脱衣",
                TargetOutput.image,
                settings,
            )
            is True
        )

    asyncio.run(scenario())


def test_regeneration_callback_is_rate_limited_per_source_task() -> None:
    async def scenario() -> None:
        await _clear_pending_followup_state_for_tests()
        settings = Settings(telegram_regenerate_cooldown_seconds=45)

        first = _callback_query(callback_query_id="callback-1", source_task_id="task-a")
        second = _callback_query(callback_query_id="callback-2", source_task_id="task-a")
        other_source = _callback_query(callback_query_id="callback-3", source_task_id="task-b")

        assert await _is_duplicate_regeneration_request(first, "task-a", settings) is False
        assert await _is_duplicate_regeneration_request(second, "task-a", settings) is True
        assert await _is_duplicate_regeneration_request(other_source, "task-b", settings) is False

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


def test_status_message_reports_bot_and_dependencies(monkeypatch) -> None:
    async def fake_http_status_label(url: str) -> str:
        if url.endswith("/system_stats"):
            return "在线"
        return "离线"

    monkeypatch.setattr(
        "app.services.task_runner._http_status_label",
        fake_http_status_label,
    )

    settings = Settings(
        llm_provider="ollama",
        llm_vision_provider="none",
        telegram_bot_token="test-token",
    )

    message = asyncio.run(_build_status_message(settings))

    assert "Bot：在线" in message
    assert "ComfyUI：在线" in message
    assert "Ollama：离线" in message
    assert "Telegram 轮询和消息处理在线" in message


def test_complete_task_success_does_not_use_detached_task_id(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with test_session_local() as db:
        user = User(telegram_id="123", credit_balance=5)
        workflow = Workflow(
            name="Flux Edit",
            kind=TaskKind.image_edit,
            comfy_workflow_key="flux-edit",
            credit_cost=1,
            description=None,
            template={},
        )
        db.add_all([user, workflow])
        db.flush()
        task = GenerationTask(
            user_id=user.id,
            workflow_id=workflow.id,
            kind=TaskKind.image_edit,
            status=TaskStatus.running,
            original_text="改图 把背景换成海边",
            interpreted_prompt=None,
            source_media_url=None,
            result_urls=[],
            credit_cost=1,
            error_message=None,
            external_job_id="prompt-1",
            telegram_chat_id="456",
            telegram_message_id="10",
        )
        db.add(task)
        db.commit()
        task_id = task.id
        public_task_id = task.public_id

    class FakeTelegram:
        def __init__(self) -> None:
            self.caption = None
            self.reply_markup = None

        async def send_result_media(
            self,
            chat_id,
            result_urls,
            kind,
            reply_to_message_id,
            *,
            caption=None,
            reply_markup=None,
        ) -> None:
            self.caption = caption
            self.reply_markup = reply_markup

        async def send_message(self, *args, **kwargs) -> None:
            raise AssertionError("success path should not send an error message")

    telegram = FakeTelegram()
    monkeypatch.setattr("app.services.task_runner.SessionLocal", test_session_local)

    asyncio.run(
        _complete_task_success(
            task_id=task_id,
            result_urls=["http://example.test/result.png"],
            telegram=telegram,
            chat_id="456",
            reply_to_message_id="10",
            kind=TaskKind.image_edit,
        )
    )

    assert telegram.reply_markup is not None
    assert public_task_id in str(telegram.reply_markup)
    assert telegram.caption == f"生成结果\n任务 ID: {public_task_id}"


def test_complete_task_success_includes_prompt_for_admin_user(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with test_session_local() as db:
        user = User(telegram_id="123", credit_balance=5, is_admin=True)
        workflow = Workflow(
            name="Flux Edit",
            kind=TaskKind.image_edit,
            comfy_workflow_key="flux-edit",
            credit_cost=1,
            description=None,
            template={},
        )
        db.add_all([user, workflow])
        db.flush()
        task = GenerationTask(
            user_id=user.id,
            workflow_id=workflow.id,
            kind=TaskKind.image_edit,
            status=TaskStatus.running,
            original_text="改图 把背景换成海边",
            interpreted_prompt="change the background to a beach",
            source_media_url=None,
            result_urls=[],
            credit_cost=1,
            error_message=None,
            external_job_id="prompt-1",
            telegram_chat_id="456",
            telegram_message_id="10",
        )
        db.add(task)
        db.commit()
        task_id = task.id
        public_task_id = task.public_id

    class FakeTelegram:
        def __init__(self) -> None:
            self.caption = None

        async def send_result_media(
            self,
            chat_id,
            result_urls,
            kind,
            reply_to_message_id,
            *,
            caption=None,
            reply_markup=None,
        ) -> None:
            self.caption = caption

        async def send_message(self, *args, **kwargs) -> None:
            raise AssertionError("success path should not send an error message")

    telegram = FakeTelegram()
    monkeypatch.setattr("app.services.task_runner.SessionLocal", test_session_local)

    asyncio.run(
        _complete_task_success(
            task_id=task_id,
            result_urls=["http://example.test/result.png"],
            telegram=telegram,
            chat_id="456",
            reply_to_message_id="10",
            kind=TaskKind.image_edit,
        )
    )

    assert telegram.caption == (
        f"生成结果\n任务 ID: {public_task_id}\n提示词: change the background to a beach"
    )


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


def _callback_query(
    *,
    callback_query_id: str,
    source_task_id: str,
) -> TelegramCallbackQuery:
    return TelegramCallbackQuery(
        callback_query_id=callback_query_id,
        telegram_id="123",
        chat_id="456",
        message_id="10",
        username="alice",
        display_name="Alice",
        data=f"regenerate:{source_task_id}",
    )
