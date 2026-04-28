from dataclasses import dataclass
from pathlib import PurePosixPath

import httpx

from app.core.config import Settings
from app.models import TaskKind


@dataclass(frozen=True)
class TelegramInboundMessage:
    telegram_id: str
    chat_id: str
    message_id: str
    username: str | None
    display_name: str | None
    text: str | None
    source_file_id: str | None


class TelegramUpdateError(ValueError):
    pass


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
        source_file_id = self._extract_source_file_id(message)

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
        )

    def _extract_source_file_id(self, message: dict) -> str | None:
        photos = message.get("photo")
        if isinstance(photos, list) and photos:
            largest = max(photos, key=lambda item: item.get("file_size") or 0)
            return largest.get("file_id")

        document = message.get("document")
        if isinstance(document, dict) and self._is_supported_document(document):
            return document.get("file_id")

        video = message.get("video")
        if isinstance(video, dict):
            return video.get("file_id")

        animation = message.get("animation")
        if isinstance(animation, dict):
            return animation.get("file_id")

        return None

    def _is_supported_document(self, document: dict) -> bool:
        mime_type = str(document.get("mime_type") or "")
        return mime_type.startswith("image/") or mime_type.startswith("video/")

    async def get_file_url(self, file_id: str) -> str:
        data = await self._request("getFile", {"file_id": file_id})
        file_path = data.get("file_path")
        if not file_path:
            raise TelegramUpdateError("Telegram did not return file_path.")
        return f"{self.file_base_url}/{file_path}"

    async def send_message(self, chat_id: str, text: str, reply_to_message_id: str | None = None) -> None:
        payload: dict[str, str | int] = {"chat_id": chat_id, "text": text}
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
        if kind == TaskKind.video_image_to_video:
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

        files = {file_field: (filename, media_bytes, content_type)}
        async with httpx.AsyncClient(base_url=self.api_base_url, timeout=60) as client:
            response = await client.post(f"/{method}", data=data, files=files)
            response.raise_for_status()
            body = response.json()
        if not body.get("ok"):
            raise TelegramUpdateError(str(body))

    async def _request(self, method: str, payload: dict) -> dict:
        async with httpx.AsyncClient(base_url=self.api_base_url, timeout=30) as client:
            response = await client.post(f"/{method}", json=payload)
            response.raise_for_status()
            body = response.json()
        if not body.get("ok"):
            raise TelegramUpdateError(str(body))
        return body.get("result") or {}
