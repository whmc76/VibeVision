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

    async def classify(self, text: str | None, has_image: bool) -> IntentResult:
        content = (text or "").strip()
        if not content and has_image:
            return IntentResult(
                kind=TaskKind.prompt_expand,
                prompt="Describe the uploaded image and suggest a detailed generation prompt.",
                confidence=0.55,
            )

        try:
            return await self._classify_with_ollama(content, has_image)
        except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError):
            return self._fallback_classify(content, has_image)

    async def _classify_with_ollama(self, text: str, has_image: bool) -> IntentResult:
        system_prompt = (
            "You route AI creative requests. Return compact JSON with keys kind, prompt, confidence. "
            "Allowed kind values: image.generate, image.edit, video.image_to_video, prompt.expand. "
            "If an image is attached and user asks motion/video, use video.image_to_video. "
            "If an image is attached and user asks edit/change, use image.edit. "
            "If the user only needs prompt help, use prompt.expand."
        )
        payload = {
            "model": self.settings.ollama_model,
            "stream": False,
            "format": "json",
            "prompt": (
                f"{system_prompt}\n\nhas_image={has_image}\nuser_request={text}\n"
                "JSON:"
            ),
        }
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
