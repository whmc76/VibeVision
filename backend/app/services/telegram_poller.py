import asyncio
import logging
import os
from pathlib import Path
from types import TracebackType

from app.core.config import ROOT_DIR, Settings, get_settings
from app.services.task_runner import process_telegram_update
from app.services.telegram import TelegramClient, TelegramUpdateError

logger = logging.getLogger(__name__)


class SingleInstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def __enter__(self) -> "SingleInstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+", encoding="utf-8")
        self._file.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                self._file.close()
                raise RuntimeError("Telegram poller is already running.") from exc
        else:
            import fcntl

            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self._file.close()
                raise RuntimeError("Telegram poller is already running.") from exc

        self._file.seek(0)
        self._file.truncate()
        self._file.write(str(os.getpid()))
        self._file.flush()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._file:
            return
        if os.name == "nt":
            import msvcrt

            self._file.seek(0)
            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()


class TelegramPoller:
    def __init__(self, settings: Settings, max_workers: int = 1):
        self.settings = settings
        self.telegram = TelegramClient(settings)
        self._semaphore = asyncio.Semaphore(max_workers)
        self._tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        await self.telegram.delete_webhook(drop_pending_updates=False)
        await self.telegram.set_my_commands()
        logger.info("Telegram webhook disabled; starting local getUpdates polling.")

        offset: int | None = None
        while True:
            try:
                updates = await self.telegram.get_updates(offset=offset, timeout=30, limit=20)
            except TelegramUpdateError:
                logger.exception("Telegram getUpdates failed.")
                await asyncio.sleep(5)
                continue

            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                task = asyncio.create_task(self._handle_update(update))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _handle_update(self, update: dict) -> None:
        async with self._semaphore:
            try:
                await process_telegram_update(update, self.settings)
            except Exception:
                logger.exception("Telegram update processing failed.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    lock_path = ROOT_DIR / "data" / "telegram-poller.lock"
    try:
        with SingleInstanceLock(lock_path):
            asyncio.run(TelegramPoller(get_settings()).run())
    except RuntimeError as exc:
        logger.error(str(exc))
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
