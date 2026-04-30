import asyncio

import pytest

from app.core.config import Settings
from app.services.telegram import (
    MAX_RESULT_CAPTION_CHARS,
    TelegramClient,
    TelegramUpdateError,
    build_regenerate_result_markup,
    build_result_caption,
)


def build_client() -> TelegramClient:
    return TelegramClient(Settings(telegram_bot_token="test-token"))


def test_parse_message_uses_reply_photo_as_source_media() -> None:
    inbound = build_client().parse_message(
        {
            "message": {
                "message_id": 8,
                "from": {"id": 123, "username": "alice"},
                "chat": {"id": 456},
                "text": "生图 让她换衣服",
                "reply_to_message": {
                    "message_id": 7,
                    "photo": [
                        {"file_id": "small", "file_size": 100},
                        {"file_id": "large", "file_size": 200},
                    ],
                },
            }
        }
    )

    assert inbound.text == "生图 让她换衣服"
    assert inbound.source_file_id == "large"
    assert inbound.source_media_type == "image"


def test_parse_callback_query_reads_regenerate_payload() -> None:
    callback = build_client().parse_callback_query(
        {
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 123, "username": "alice", "first_name": "Alice"},
                "message": {"message_id": 8, "chat": {"id": 456}},
                "data": "regenerate:42",
            }
        }
    )

    assert callback is not None
    assert callback.callback_query_id == "callback-1"
    assert callback.telegram_id == "123"
    assert callback.chat_id == "456"
    assert callback.message_id == "8"
    assert callback.username == "alice"
    assert callback.display_name == "Alice"
    assert callback.data == "regenerate:42"


def test_request_sync_preserves_empty_result_list() -> None:
    client = build_client()
    client._execute_request_with_retry = lambda build_request, timeout: {"ok": True, "result": []}  # type: ignore[method-assign]

    assert client._request_sync("getUpdates", {}, 30) == []


def test_request_sync_can_skip_retry_for_non_idempotent_requests() -> None:
    client = build_client()
    calls = 0

    def execute_once(_request, timeout: int) -> dict:
        nonlocal calls
        calls += 1
        raise TelegramUpdateError("Telegram API request timed out.")

    client._execute_request = execute_once  # type: ignore[method-assign]

    with pytest.raises(TelegramUpdateError):
        client._request_sync("sendMessage", {"chat_id": "456", "text": "hello"}, 30, retry=False)

    assert calls == 1


def test_send_message_disables_retry_to_avoid_duplicate_chat_messages() -> None:
    client = build_client()
    captured: dict[str, object] = {}

    async def fake_request(
        method: str,
        payload: dict,
        timeout: int = 30,
        *,
        retry: bool = True,
    ) -> dict:
        captured.update(
            {
                "method": method,
                "payload": payload,
                "timeout": timeout,
                "retry": retry,
            }
        )
        return {}

    client._request = fake_request  # type: ignore[method-assign]

    asyncio.run(client.send_message("456", "hello", "8"))

    assert captured["method"] == "sendMessage"
    assert captured["retry"] is False
    assert captured["payload"] == {
        "chat_id": "456",
        "text": "hello",
        "reply_markup": {"remove_keyboard": True},
        "reply_to_message_id": 8,
    }


def test_send_result_media_adds_regenerate_button_to_first_result() -> None:
    client = build_client()
    calls: list[dict[str, object]] = []
    markup = build_regenerate_result_markup(42)

    async def fake_send_url_as_upload(**kwargs):
        calls.append(kwargs)

    client._send_url_as_upload = fake_send_url_as_upload  # type: ignore[method-assign]

    asyncio.run(
        client.send_result_media(
            "456",
            ["https://example.test/one.png", "https://example.test/two.png"],
            kind="image.generate",
            reply_to_message_id="8",
            caption="生成结果",
            reply_markup=markup,
        )
    )

    assert calls[0]["reply_markup"] == markup
    assert calls[1]["reply_markup"] is None


def test_build_regenerate_result_markup_uses_task_id() -> None:
    assert build_regenerate_result_markup(42) == {
        "inline_keyboard": [[{"text": "重新生成", "callback_data": "regenerate:42"}]]
    }


def test_build_result_caption_hides_prompt_by_default() -> None:
    caption = build_result_caption("  cinematic portrait\nsoft light  ", task_id=42)

    assert caption == "生成结果\n任务 ID: 42"


def test_build_result_caption_can_include_task_id_and_prompt() -> None:
    caption = build_result_caption(
        "  cinematic portrait\nsoft light  ",
        task_id=42,
        include_prompt=True,
    )

    assert caption == "生成结果\n任务 ID: 42\n提示词: cinematic portrait soft light"


def test_build_result_caption_truncates_long_prompt() -> None:
    caption = build_result_caption("x" * 2_000, task_id=42, include_prompt=True)

    assert len(caption) == MAX_RESULT_CAPTION_CHARS
    assert caption.endswith("...")
