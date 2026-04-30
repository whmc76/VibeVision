import asyncio
import logging
import os
import sys
from pathlib import Path
from types import TracebackType

from sqlalchemy import select

from app.core.config import ROOT_DIR, Settings, get_settings
from app.db.session import SessionLocal
from app.models import GenerationTask, TaskStatus
from app.services.comfyui import ComfyUIClient
from app.services.orchestrator import GenerationOrchestrator
from app.services.task_runner import complete_comfyui_task
from app.services.telegram import TelegramClient

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
                raise RuntimeError("Task queue worker is already running.") from exc
        else:
            import fcntl

            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self._file.close()
                raise RuntimeError("Task queue worker is already running.") from exc

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


class TaskQueueWorker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.orchestrator = GenerationOrchestrator(settings)
        self.comfyui = ComfyUIClient(settings)
        self.telegram = TelegramClient(settings)

    async def run(self) -> None:
        logger.info("Task queue worker started.")
        while True:
            processed = await self.process_next()
            if not processed:
                await asyncio.sleep(1)

    async def process_next(self) -> bool:
        task_id = self._next_task_id()
        if not task_id:
            return False
        try:
            await self.comfyui.wait_until_ready(self.settings.comfyui_startup_wait_seconds)
        except Exception:
            logger.exception(
                "ComfyUI is not ready; keeping queued task %s pending.", task_id
            )
            await asyncio.sleep(5)
            return False
        try:
            await self.process_task(task_id)
        except Exception:
            logger.exception("Task queue worker failed while processing task %s.", task_id)
        return True

    def _next_task_id(self) -> int | None:
        with SessionLocal() as db:
            return db.scalar(
                select(GenerationTask.id)
                .where(GenerationTask.status == TaskStatus.queued)
                .order_by(GenerationTask.created_at, GenerationTask.id)
                .limit(1)
            )

    async def process_task(self, task_id: int) -> None:
        with SessionLocal() as db:
            task = await self.orchestrator.execute_queued_task(db, task_id)
            status = task.status
            prompt_id = task.external_job_id
            chat_id = task.telegram_chat_id
            reply_to_message_id = task.telegram_message_id
            kind = task.kind
            error_message = task.error_message
            public_task_id = task.public_id

        if status == TaskStatus.failed or not prompt_id:
            if chat_id:
                await self.telegram.send_message(
                    chat_id,
                    f"任务 #{public_task_id} 执行失败，积分已退回。{error_message or ''}".strip(),
                    reply_to_message_id,
                )
            return

        await complete_comfyui_task(
            task_id=task_id,
            prompt_id=prompt_id,
            chat_id=chat_id or "",
            reply_to_message_id=reply_to_message_id or "",
            kind=kind,
            settings=self.settings,
        )


def main() -> None:
    log_path = ROOT_DIR / "data" / "task-queue-worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
        force=True,
    )
    lock_path = ROOT_DIR / "data" / "task-queue-worker.lock"
    try:
        with SingleInstanceLock(lock_path):
            asyncio.run(TaskQueueWorker(get_settings()).run())
    except RuntimeError as exc:
        logger.error(str(exc))
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
