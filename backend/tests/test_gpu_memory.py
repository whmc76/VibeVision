import asyncio
from uuid import uuid4

from app.services.gpu_memory import gpu_resource_scope, schedule_gpu_resource_release


def test_gpu_resource_scope_releases_after_idle_delay() -> None:
    calls: list[str] = []

    async def release() -> None:
        calls.append("release")

    async def scenario() -> None:
        async with gpu_resource_scope(f"test-{uuid4().hex}", 0, release):
            pass
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert calls == ["release"]


def test_gpu_resource_scope_cancels_stale_release_when_work_resumes() -> None:
    calls: list[str] = []
    key = f"test-{uuid4().hex}"

    async def release() -> None:
        calls.append("release")

    async def scenario() -> None:
        async with gpu_resource_scope(key, 1, release):
            pass
        async with gpu_resource_scope(key, 1, release):
            await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert calls == []


def test_scheduled_gpu_release_waits_for_active_scope() -> None:
    calls: list[str] = []
    key = f"test-{uuid4().hex}"

    async def release() -> None:
        calls.append("release")

    async def scenario() -> None:
        async with gpu_resource_scope(key, 0, release):
            schedule_gpu_resource_release(key, 0, release)
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert calls == ["release"]
