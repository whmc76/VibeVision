import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.services.concurrency import concurrency_slot

logger = logging.getLogger(__name__)

ReleaseCallback = Callable[[], Awaitable[None]]


@dataclass
class _IdleReleaseState:
    active_count: int = 0
    generation: int = 0
    release_task: asyncio.Task[None] | None = None


_IDLE_RELEASE_STATES: dict[tuple[int, str], _IdleReleaseState] = {}
_ACTIVE_GPU_RESOURCES: ContextVar[dict[str, int] | None] = ContextVar(
    "active_gpu_resources",
    default=None,
)


def _state_for(key: str) -> _IdleReleaseState:
    state_key = (id(asyncio.get_running_loop()), key)
    state = _IDLE_RELEASE_STATES.get(state_key)
    if state is None:
        state = _IdleReleaseState()
        _IDLE_RELEASE_STATES[state_key] = state
    return state


@asynccontextmanager
async def gpu_resource_scope(
    key: str,
    idle_release_seconds: int,
    release: ReleaseCallback,
) -> AsyncIterator[None]:
    active_resources = _ACTIVE_GPU_RESOURCES.get() or {}
    reentrant = active_resources.get(key, 0) > 0
    state = _state_for(key)

    if not reentrant:
        _cancel_release(state)
        state.active_count += 1
        state.generation += 1

    updated_resources = dict(active_resources)
    updated_resources[key] = updated_resources.get(key, 0) + 1
    token = _ACTIVE_GPU_RESOURCES.set(updated_resources)
    try:
        yield
    finally:
        _ACTIVE_GPU_RESOURCES.reset(token)
        if not reentrant:
            state.active_count = max(0, state.active_count - 1)
            state.generation += 1
            if state.active_count == 0:
                _schedule_release(
                    key=key,
                    state=state,
                    idle_release_seconds=idle_release_seconds,
                    release=release,
                )


def _cancel_release(state: _IdleReleaseState) -> None:
    task = state.release_task
    if task and not task.done():
        task.cancel()
    state.release_task = None


def _schedule_release(
    *,
    key: str,
    state: _IdleReleaseState,
    idle_release_seconds: int,
    release: ReleaseCallback,
) -> None:
    delay = max(0, int(idle_release_seconds))
    generation = state.generation

    async def delayed_release() -> None:
        try:
            if delay:
                await asyncio.sleep(delay)
            if state.active_count != 0 or state.generation != generation:
                return
            await release()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Failed to release idle GPU resource %s.", key, exc_info=True)
        finally:
            if state.release_task is asyncio.current_task():
                state.release_task = None

    state.release_task = asyncio.create_task(
        delayed_release(),
        name=f"vibevision-release-{key}-gpu-memory",
    )


def schedule_gpu_resource_release(
    key: str,
    idle_release_seconds: int,
    release: ReleaseCallback,
) -> None:
    state = _state_for(key)
    if state.active_count == 0:
        _schedule_release(
            key=key,
            state=state,
            idle_release_seconds=idle_release_seconds,
            release=release,
        )


def ollama_keep_alive_value(settings: Settings) -> str:
    return f"{max(0, int(settings.gpu_idle_release_seconds))}s"


def ollama_gpu_scope(settings: Settings) -> AsyncIterator[None]:
    return gpu_resource_scope(
        "ollama",
        settings.gpu_idle_release_seconds,
        lambda: release_ollama_gpu_memory(settings),
    )


def comfyui_gpu_scope(settings: Settings) -> AsyncIterator[None]:
    return gpu_resource_scope(
        "comfyui",
        settings.gpu_idle_release_seconds,
        lambda: release_comfyui_gpu_memory(settings),
    )


def schedule_comfyui_gpu_release(settings: Settings) -> None:
    schedule_gpu_resource_release(
        "comfyui",
        settings.gpu_idle_release_seconds,
        lambda: release_comfyui_gpu_memory(settings),
    )


async def release_ollama_gpu_memory(settings: Settings) -> None:
    models = _ollama_models_for_idle_release(settings)
    if not models:
        return

    async with concurrency_slot("ollama", settings.ollama_max_concurrency):
        async with httpx.AsyncClient(
            base_url=settings.ollama_base_url,
            timeout=15,
            limits=_single_connection_limits(),
        ) as client:
            loaded_models = await _loaded_ollama_models(client)
            target_models = models
            if loaded_models is not None:
                target_models = [model for model in models if model in loaded_models]
            for model in target_models:
                try:
                    response = await client.post(
                        "/api/generate",
                        json={
                            "model": model,
                            "prompt": "",
                            "stream": False,
                            "keep_alive": 0,
                        },
                    )
                    response.raise_for_status()
                except httpx.HTTPError:
                    logger.warning("Failed to unload idle Ollama model %s.", model, exc_info=True)


async def _loaded_ollama_models(client: httpx.AsyncClient) -> set[str] | None:
    try:
        response = await client.get("/api/ps")
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError:
        return None
    except ValueError:
        return None

    loaded: set[str] = set()
    for item in body.get("models", []):
        if not isinstance(item, dict):
            continue
        for key in ("model", "name"):
            value = item.get(key)
            if isinstance(value, str) and value:
                loaded.add(value)
    return loaded


def _ollama_models_for_idle_release(settings: Settings) -> list[str]:
    models: list[str] = []
    if settings.llm_logic_provider_name == "ollama" or settings.llm_vision_provider_name == "ollama":
        models.append(settings.ollama_logic_model_name)
    if settings.llm_prompt_provider_name == "ollama":
        models.append(settings.ollama_prompt_model_name)

    unique_models: list[str] = []
    for model in models:
        if model and model not in unique_models:
            unique_models.append(model)
    return unique_models


async def release_comfyui_gpu_memory(settings: Settings) -> None:
    async with concurrency_slot("comfyui", settings.comfyui_max_concurrency):
        async with httpx.AsyncClient(
            base_url=settings.comfyui_base_url,
            timeout=30,
            limits=_single_connection_limits(),
        ) as client:
            if await _comfyui_queue_has_work(client):
                schedule_comfyui_gpu_release(settings)
                return
            response = await client.post(
                "/free",
                json={"unload_models": True, "free_memory": True},
            )
            response.raise_for_status()


async def _comfyui_queue_has_work(client: httpx.AsyncClient) -> bool:
    try:
        response = await client.get("/queue")
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    return bool(body.get("queue_running") or body.get("queue_pending"))


def _single_connection_limits() -> httpx.Limits:
    return httpx.Limits(max_connections=1, max_keepalive_connections=1)
