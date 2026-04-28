from typing import Any
from uuid import uuid4

import httpx

from app.core.config import Settings
from app.models import Workflow


class ComfyUIClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def submit_prompt(
        self,
        workflow: Workflow,
        prompt: str,
        source_media_url: str | None,
    ) -> str:
        payload = self._build_payload(workflow, prompt, source_media_url)
        async with httpx.AsyncClient(base_url=self.settings.comfyui_base_url, timeout=30) as client:
            response = await client.post("/prompt", json=payload)
            response.raise_for_status()
            data = response.json()
        return str(data.get("prompt_id") or data.get("id") or uuid4())

    def _build_payload(
        self,
        workflow: Workflow,
        prompt: str,
        source_media_url: str | None,
    ) -> dict[str, Any]:
        template = workflow.template or {}
        return {
            "client_id": "vibevision-api",
            "prompt": {
                **template,
                "_vibevision": {
                    "workflow_key": workflow.comfy_workflow_key,
                    "prompt": prompt,
                    "source_media_url": source_media_url,
                },
            },
        }
