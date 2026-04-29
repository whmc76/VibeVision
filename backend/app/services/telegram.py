import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import httpx

from app.core.config import Settings
from app.models import TaskKind


def build_remove_keyboard_markup() -> dict[str, bool]:
    return {"remove_keyboard": True}


def build_bot_commands() -> list[dict[str, str]]:
    return [
        {"command": "photo", "description": "照片工作流"},
        {"command": "p", "description": "照片工作流快捷命令"},
        {"command": "video", "description": "视频工作流"},
        {"command": "v", "description": "视频工作流快捷命令"},
        {"command": "check", "description": "套餐和剩余点数"},
        {"command": "start", "description": "欢迎与使用说明"},
    ]


@dataclass(frozen=True)
class TelegramInboundMessage:
    telegram_id: str
    chat_id: str
    message_id: str
    username: str | None
    display_name: str | None
    text: str | None
    source_file_id: str | None
    source_media_type: str | None


@dataclass(frozen=True)
class TelegramWebhookInfo:
    url: str | None
    pending_update_count: int
    has_custom_certificate: bool
    last_error_date: int | None
    last_error_message: str | None
    ip_address: str | None
    max_connections: int | None
    allowed_updates: list[str]


class TelegramUpdateError(ValueError):
    pass


REQUEST_RETRY_DELAYS = (1.0, 2.0)
UPLOAD_TIMEOUT_SECONDS = 180


class TelegramClient:
    def __init__(self, settings: Settings):
        if not settings.telegram_bot_token:
            raise TelegramUpdateError("TELEGRAM_BOT_TOKEN is not configured.")
        self.token = settings.telegram_bot_token
        self.api_base_url = f"https://api.telegram.org/bot{self.token}"
        self.file_base_url = f"https://api.telegram.org/file/bot{self.token}"

    def parse_message(self, update: dict) -> TelegramInboundMessage:
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            raise TelegramUpdateError("Unsupported Telegram update: no message payload.")

        from_user = message.get("from") or {}
        chat = message.get("chat") or {}
        telegram_id = from_user.get("id")
        chat_id = chat.get("id")
        message_id = message.get("message_id")
        if telegram_id is None or chat_id is None or message_id is None:
            raise TelegramUpdateError("Telegram update is missing identity fields.")

        text = message.get("text") or message.get("caption")
        source_file_id, source_media_type = self._extract_source_media(message)
        if not source_file_id:
            reply_to_message = message.get("reply_to_message")
            if isinstance(reply_to_message, dict):
                source_file_id, source_media_type = self._extract_source_media(reply_to_message)

        first_name = from_user.get("first_name") or ""
        last_name = from_user.get("last_name") or ""
        display_name = " ".join(part for part in [first_name, last_name] if part).strip() or None

        return TelegramInboundMessage(
            telegram_id=str(telegram_id),
            chat_id=str(chat_id),
            message_id=str(message_id),
            username=from_user.get("username"),
            display_name=display_name,
            text=text,
            source_file_id=source_file_id,
            source_media_type=source_media_type,
        )

    def _extract_source_media(self, message: dict) -> tuple[str | None, str | None]:
        photos = message.get("photo")
        if isinstance(photos, list) and photos:
            largest = max(photos, key=lambda item: item.get("file_size") or 0)
            return largest.get("file_id"), "image"

        document = message.get("document")
        if isinstance(document, dict) and self._is_supported_document(document):
            mime_type = str(document.get("mime_type") or "")
            media_type = "video" if mime_type.startswith("video/") else "image"
            return document.get("file_id"), media_type

        video = message.get("video")
        if isinstance(video, dict):
            return video.get("file_id"), "video"

        animation = message.get("animation")
        if isinstance(animation, dict):
            return animation.get("file_id"), "video"

        return None, None

    def _is_supported_document(self, document: dict) -> bool:
        mime_type = str(document.get("mime_type") or "")
        return mime_type.startswith("image/") or mime_type.startswith("video/")

    async def get_file_url(self, file_id: str) -> str:
        data = await self._request("getFile", {"file_id": file_id})
        file_path = data.get("file_path")
        if not file_path:
            raise TelegramUpdateError("Telegram did not return file_path.")
        return f"{self.file_base_url}/{file_path}"

    async def get_webhook_info(self) -> TelegramWebhookInfo:
        data = await self._request("getWebhookInfo", {})
        return TelegramWebhookInfo(
            url=data.get("url") or None,
            pending_update_count=int(data.get("pending_update_count") or 0),
            has_custom_certificate=bool(data.get("has_custom_certificate")),
            last_error_date=int(data["last_error_date"]) if data.get("last_error_date") else None,
            last_error_message=data.get("last_error_message") or None,
            ip_address=data.get("ip_address") or None,
            max_connections=int(data["max_connections"]) if data.get("max_connections") else None,
            allowed_updates=[str(item) for item in data.get("allowed_updates") or []],
        )

    async def delete_webhook(self, drop_pending_updates: bool = False) -> None:
        await self._request("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    async def set_my_commands(self) -> None:
        await self._request("setMyCommands", {"commands": build_bot_commands()})

    async def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 30,
        limit: int = 20,
    ) -> list[dict]:
        payload: dict[str, int | list[str]] = {
            "timeout": timeout,
            "limit": limit,
            "allowed_updates": ["message", "edited_message", "my_chat_member"],
        }
        if offset is not None:
            payload["offset"] = offset
        data = await self._request("getUpdates", payload, timeout=timeout + 10)
        if not isinstance(data, list):
            raise TelegramUpdateError(f"Telegram returned invalid getUpdates result: {data}")
        return [item for item in data if isinstance(item, dict)]

    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None = None,
        reply_markup: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup or build_remove_keyboard_markup(),
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = int(reply_to_message_id)
        await self._request("sendMessage", payload)

    async def send_result_media(
        self,
        chat_id: str,
        urls: list[str],
        kind: TaskKind,
        reply_to_message_id: str | None = None,
    ) -> None:
        if not urls:
            await self.send_message(chat_id, "任务完成，但没有找到输出文件。", reply_to_message_id)
            return

        for index, url in enumerate(urls[:4], start=1):
            await self._send_url_as_upload(
                chat_id=chat_id,
                url=url,
                kind=kind,
                caption="生成结果" if index == 1 else None,
                reply_to_message_id=reply_to_message_id if index == 1 else None,
            )

    async def _send_url_as_upload(
        self,
        chat_id: str,
        url: str,
        kind: TaskKind,
        caption: str | None,
        reply_to_message_id: str | None,
    ) -> None:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url)
            response.raise_for_status()
            media_bytes = response.content
            content_type = response.headers.get("content-type", "application/octet-stream")

        filename = PurePosixPath(url.split("?", 1)[0]).name or "vibevision-output"
        if kind in {TaskKind.video_text_to_video, TaskKind.video_image_to_video}:
            method = "sendVideo"
            file_field = "video"
        elif content_type.startswith("image/"):
            method = "sendPhoto"
            file_field = "photo"
        else:
            method = "sendDocument"
            file_field = "document"

        data: dict[str, str | int] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        if reply_to_message_id:
            data["reply_to_message_id"] = int(reply_to_message_id)

        await self._upload_request(
            method=method,
            data=data,
            file_field=file_field,
            filename=filename,
            media_bytes=media_bytes,
            content_type=content_type,
        )

    async def _request(self, method: str, payload: dict, timeout: int = 30) -> Any:
        return await asyncio.to_thread(self._request_sync, method, payload, timeout)

    def _request_sync(self, method: str, payload: dict, timeout: int) -> Any:
        body = self._execute_request_with_retry(
            lambda: self._build_json_request(method, payload),
            timeout=timeout,
        )
        return body.get("result") if "result" in body else {}

    async def _upload_request(
        self,
        method: str,
        data: dict[str, str | int],
        file_field: str,
        filename: str,
        media_bytes: bytes,
        content_type: str,
    ) -> dict:
        return await asyncio.to_thread(
            self._upload_request_sync,
            method,
            data,
            file_field,
            filename,
            media_bytes,
            content_type,
        )

    def _upload_request_sync(
        self,
        method: str,
        data: dict[str, str | int],
        file_field: str,
        filename: str,
        media_bytes: bytes,
        content_type: str,
    ) -> dict:
        boundary = f"----VibeVision{uuid4().hex}"
        body = self._encode_multipart_form_data(
            boundary=boundary,
            data=data,
            file_field=file_field,
            filename=filename,
            media_bytes=media_bytes,
            content_type=content_type,
        )
        response = self._execute_request_with_retry(
            lambda: Request(
                url=f"{self.api_base_url}/{method}",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            ),
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
        return response.get("result") or {}

    def _build_json_request(self, method: str, payload: dict) -> Request:
        if payload:
            return Request(
                url=f"{self.api_base_url}/{method}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        return Request(url=f"{self.api_base_url}/{method}", method="GET")

    def _execute_request_with_retry(
        self,
        build_request: Callable[[], Request],
        timeout: int = 30,
    ) -> dict:
        last_error: TelegramUpdateError | None = None
        for attempt in range(len(REQUEST_RETRY_DELAYS) + 1):
            try:
                return self._execute_request(build_request(), timeout=timeout)
            except TelegramUpdateError as exc:
                last_error = exc
                if attempt >= len(REQUEST_RETRY_DELAYS) or not self._is_retryable_error(exc):
                    raise
                time.sleep(REQUEST_RETRY_DELAYS[attempt])

        if last_error:
            raise last_error
        raise TelegramUpdateError("Telegram API request failed.")

    def _execute_request(self, request: Request, timeout: int = 30) -> dict:
        try:
            with urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramUpdateError(f"Telegram API HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise TelegramUpdateError(f"Telegram API network error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TelegramUpdateError("Telegram API request timed out.") from exc

        if not body.get("ok"):
            raise TelegramUpdateError(str(body))
        return body

    def _is_retryable_error(self, exc: TelegramUpdateError) -> bool:
        cause = exc.__cause__
        if isinstance(cause, HTTPError):
            return 500 <= cause.code < 600
        if isinstance(cause, (TimeoutError, URLError)):
            return True

        message = str(exc).lower()
        return any(
            token in message
            for token in (
                "timed out",
                "timeout",
                "temporarily unavailable",
                "connection reset",
                "connection aborted",
            )
        )

    def _encode_multipart_form_data(
        self,
        boundary: str,
        data: dict[str, str | int],
        file_field: str,
        filename: str,
        media_bytes: bytes,
        content_type: str,
    ) -> bytes:
        parts: list[bytes] = []

        for key, value in data.items():
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            parts.append(str(value).encode("utf-8"))
            parts.append(b"\r\n")

        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            (
                f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
        )
        parts.append(media_bytes)
        parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        return b"".join(parts)
