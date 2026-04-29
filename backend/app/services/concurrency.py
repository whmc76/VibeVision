import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

_SEMAPHORES: dict[tuple[int, str, int], asyncio.Semaphore] = {}
_ACTIVE_KEYS: ContextVar[dict[str, int] | None] = ContextVar(
    "active_concurrency_keys",
    default=None,
)


def _semaphore_for(key: str, limit: int) -> asyncio.Semaphore:
    normalized_limit = max(1, int(limit or 1))
    loop_id = id(asyncio.get_running_loop())
    semaphore_key = (loop_id, key, normalized_limit)
    semaphore = _SEMAPHORES.get(semaphore_key)
    if semaphore is None:
        semaphore = asyncio.Semaphore(normalized_limit)
        _SEMAPHORES[semaphore_key] = semaphore
    return semaphore


@asynccontextmanager
async def concurrency_slot(key: str, limit: int) -> AsyncIterator[None]:
    active_keys = _ACTIVE_KEYS.get() or {}
    if active_keys.get(key, 0) > 0:
        yield
        return

    async with _semaphore_for(key, limit):
        updated_keys = dict(active_keys)
        updated_keys[key] = updated_keys.get(key, 0) + 1
        token = _ACTIVE_KEYS.set(updated_keys)
        try:
            yield
        finally:
            _ACTIVE_KEYS.reset(token)
