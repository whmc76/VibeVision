import base64
import json
from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.models import TaskKind


@dataclass(frozen=True)
class IntentResult:
    kind: TaskKind
    prompt: str
    confidence: float


class IntentService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def classify(
        self,
        text: str | None,
        has_image: bool = False,
        source_media_url: str | None = None,
    ) -> IntentResult:
        content = (text or "").strip()
        images = await self._load_ollama_images(source_media_url) if source_media_url else []
        media_attached = has_image or bool(source_media_url)

        if not content and media_attached and not images:
            return IntentResult(
                kind=TaskKind.prompt_expand,
                prompt="Describe the uploaded media and suggest a detailed generation prompt.",
                confidence=0.55,
            )

        try:
            return await self._classify_with_ollama(
                text=content,
                media_attached=media_attached,
                images=images,
            )
        except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError):
            return self._fallback_classify(content, media_attached)

    async def _classify_with_ollama(
        self,
        text: str,
        media_attached: bool,
        images: list[str],
    ) -> IntentResult:
        system_prompt = (
            "You route AI creative requests. Return compact JSON with keys kind, prompt, confidence. "
            "Allowed kind values: image.generate, image.edit, video.image_to_video, prompt.expand. "
            "If media is attached and user asks motion/video, use video.image_to_video. "
            "If media is attached and user asks edit/change/restyle, use image.edit. "
            "If an image is provided, inspect it and use its visible content in the prompt. "
            "If the user only needs image understanding or prompt help, use prompt.expand."
        )
        payload = {
            "model": self.settings.ollama_model,
            "stream": False,
            "format": "json",
            "prompt": (
                f"{system_prompt}\n\n"
                f"media_attached={media_attached}\n"
                f"vision_image_attached={bool(images)}\n"
                f"user_request={text or 'No text request was provided.'}\n"
                "JSON:"
            ),
        }
        if images:
            payload["images"] = images

        async with httpx.AsyncClient(base_url=self.settings.ollama_base_url, timeout=20) as client:
            response = await client.post("/api/generate", json=payload)
            response.raise_for_status()
            body = response.json()

        raw = body.get("response", "{}")
        data = json.loads(raw)
        return IntentResult(
            kind=TaskKind(data["kind"]),
            prompt=str(data.get("prompt") or text),
            confidence=float(data.get("confidence", 0.7)),
        )

    async def _load_ollama_images(self, source_media_url: str) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                async with client.stream("GET", source_media_url) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if not content_type.startswith("image/"):
                        return []

                    chunks: list[bytes] = []
                    total_bytes = 0
                    async for chunk in response.aiter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > self.settings.ollama_vision_max_bytes:
                            return []
                        chunks.append(chunk)
        except httpx.HTTPError:
            return []

        if not chunks:
            return []
        return [base64.b64encode(b"".join(chunks)).decode("ascii")]

    def _fallback_classify(self, text: str, has_image: bool) -> IntentResult:
        lowered = text.lower()
        if has_image and any(token in lowered for token in ["video", "motion", "animate", "动", "视频"]):
            kind = TaskKind.video_image_to_video
        elif has_image and any(token in lowered for token in ["edit", "change", "replace", "修改", "编辑"]):
            kind = TaskKind.image_edit
        elif any(token in lowered for token in ["prompt", "提示词", "扩写", "理解图片"]):
            kind = TaskKind.prompt_expand
        else:
            kind = TaskKind.image_generate

        return IntentResult(kind=kind, prompt=text or "Create a polished image from the user request.", confidence=0.35)
