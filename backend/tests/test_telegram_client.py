from app.core.config import Settings
from app.services.telegram import TelegramClient


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


def test_request_sync_preserves_empty_result_list() -> None:
    client = build_client()
    client._execute_request_with_retry = lambda build_request, timeout: {"ok": True, "result": []}  # type: ignore[method-assign]

    assert client._request_sync("getUpdates", {}, 30) == []
