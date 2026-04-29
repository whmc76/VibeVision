import json
import logging
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueuedTelegramUpdate:
    message_id: str
    update: dict


class TelegramUpdateQueue:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.enabled = bool(settings.telegram_update_queue_url)
        self._redis: Any | None = None
        self._group_ready = False

    async def enqueue(self, update: dict) -> str | None:
        if not self.enabled:
            return None

        redis = await self._client()
        update_id = update.get("update_id")
        fields = {
            "update_json": json.dumps(update, ensure_ascii=False, separators=(",", ":")),
            "update_id": "" if update_id is None else str(update_id),
        }
        return await redis.xadd(
            self.settings.telegram_update_queue_stream,
            fields,
            maxlen=self.settings.telegram_update_queue_maxlen,
            approximate=True,
        )

    async def read(self, consumer: str) -> QueuedTelegramUpdate | None:
        if not self.enabled:
            return None

        await self._ensure_group()
        pending = await self._read_group(consumer, stream_id="0", block_ms=None)
        if pending:
            return pending
        return await self._read_group(
            consumer,
            stream_id=">",
            block_ms=self.settings.telegram_update_queue_block_ms,
        )

    async def ack(self, message_id: str) -> None:
        if not self.enabled:
            return
        redis = await self._client()
        await redis.xack(
            self.settings.telegram_update_queue_stream,
            self.settings.telegram_update_queue_group,
            message_id,
        )

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def _client(self) -> Any:
        if self._redis is not None:
            return self._redis

        try:
            from redis import asyncio as redis
        except ImportError as exc:
            raise RuntimeError(
                "Redis queue is enabled, but the 'redis' package is not installed."
            ) from exc

        self._redis = redis.from_url(
            self.settings.telegram_update_queue_url,
            decode_responses=True,
        )
        return self._redis

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return

        redis = await self._client()
        try:
            await redis.xgroup_create(
                self.settings.telegram_update_queue_stream,
                self.settings.telegram_update_queue_group,
                id="0",
                mkstream=True,
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def _read_group(
        self,
        consumer: str,
        *,
        stream_id: str,
        block_ms: int | None,
    ) -> QueuedTelegramUpdate | None:
        redis = await self._client()
        kwargs: dict[str, Any] = {
            "groupname": self.settings.telegram_update_queue_group,
            "consumername": consumer,
            "streams": {self.settings.telegram_update_queue_stream: stream_id},
            "count": 1,
        }
        if block_ms is not None:
            kwargs["block"] = block_ms

        response = await redis.xreadgroup(**kwargs)
        if not response:
            return None

        _stream_name, messages = response[0]
        if not messages:
            return None

        message_id, fields = messages[0]
        update_json = fields.get("update_json")
        if not update_json:
            logger.warning("Telegram update queue message %s has no update_json.", message_id)
            await self.ack(message_id)
            return None

        try:
            update = json.loads(update_json)
        except json.JSONDecodeError:
            logger.exception("Telegram update queue message %s has invalid JSON.", message_id)
            await self.ack(message_id)
            return None

        if not isinstance(update, dict):
            logger.warning("Telegram update queue message %s is not a JSON object.", message_id)
            await self.ack(message_id)
            return None

        return QueuedTelegramUpdate(message_id=message_id, update=update)
