import asyncio

import httpx

from app.core.config import Settings
from app.services.comfyui import ComfyUIClient


def test_comfyui_request_retries_bad_gateway(monkeypatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.services.comfyui.asyncio.sleep", no_sleep)

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(502, request=request)
        return httpx.Response(200, request=request, json={"ok": True})

    async def run() -> None:
        comfyui = ComfyUIClient(Settings())
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8401",
            transport=transport,
        ) as client:
            response = await comfyui._request(client, "POST", "/upload/image")

        assert response.status_code == 200
        assert [request.url.path for request in requests] == ["/upload/image", "/upload/image"]

    asyncio.run(run())


def test_comfyui_request_returns_last_bad_gateway_after_retries(monkeypatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.services.comfyui.asyncio.sleep", no_sleep)

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(502, request=request)

    async def run() -> None:
        comfyui = ComfyUIClient(Settings())
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8401",
            transport=transport,
        ) as client:
            response = await comfyui._request(client, "POST", "/upload/image")

        assert response.status_code == 502
        assert len(requests) == 3

    asyncio.run(run())
