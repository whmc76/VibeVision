import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.db.session import SessionLocal, create_db_and_tables
from app.routers import admin, telegram
from app.seed import seed_defaults
from app.services.error_details import format_exception_details
from app.services.task_recovery import recover_unfinished_tasks
from app.services.task_runner import process_telegram_update
from app.services.telegram_update_queue import TelegramUpdateQueue

settings = get_settings()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router, prefix="/api")
app.include_router(telegram.router, prefix="/api")


@app.on_event("startup")
async def on_startup() -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        seed_defaults(db)
    asyncio.create_task(recover_unfinished_tasks(settings))
    if settings.telegram_update_queue_url and settings.telegram_bot_token:
        app.state.telegram_update_queue_tasks = [
            asyncio.create_task(_consume_telegram_update_queue(worker_index))
            for worker_index in range(max(1, settings.telegram_update_queue_workers))
        ]


@app.on_event("shutdown")
async def on_shutdown() -> None:
    tasks = getattr(app.state, "telegram_update_queue_tasks", [])
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _consume_telegram_update_queue(worker_index: int) -> None:
    queue = TelegramUpdateQueue(settings)
    consumer = f"{settings.telegram_update_queue_consumer_prefix}-api-{worker_index}"
    try:
        while True:
            try:
                queued = await queue.read(consumer)
            except Exception:
                logger.exception("Telegram update queue read failed for API consumer %s.", consumer)
                await asyncio.sleep(5)
                continue
            if queued is None:
                continue
            try:
                await process_telegram_update(queued.update, settings)
            except Exception:
                logger.exception(
                    "Telegram queued update processing failed for Redis message %s.",
                    queued.message_id,
                )
                await asyncio.sleep(5)
                continue
            await queue.ack(queued.message_id)
    finally:
        await queue.close()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "environment": settings.environment}


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": format_exception_details(exc)},
    )
