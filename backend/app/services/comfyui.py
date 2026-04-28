import asyncio
import copy
from typing import Any
from urllib.parse import urlencode
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
        async with httpx.AsyncClient(base_url=self.settings.comfyui_base_url, timeout=30) as client:
            source_image_name = None
            if source_media_url:
                source_image_name = await self._upload_source_image(client, source_media_url)

            payload = self._build_payload(workflow, prompt, source_image_name)
            response = await client.post("/prompt", json=payload)
            response.raise_for_status()
            data = response.json()
        return str(data.get("prompt_id") or data.get("id") or uuid4())

    def _build_payload(
        self,
        workflow: Workflow,
        prompt: str,
        source_image_name: str | None,
    ) -> dict[str, Any]:
        template = workflow.template or {}
        if template.get("prompt"):
            prompt_graph = self._apply_prompt_values(
                copy.deepcopy(template["prompt"]),
                prompt=prompt,
                source_image_name=source_image_name,
            )
            return {"client_id": "vibevision-api", "prompt": prompt_graph}

        return {
            "client_id": "vibevision-api",
            "prompt": {
                **template,
                "_vibevision": {
                    "workflow_key": workflow.comfy_workflow_key,
                    "prompt": prompt,
                    "source_image_name": source_image_name,
                },
            },
        }

    async def _upload_source_image(self, client: httpx.AsyncClient, source_media_url: str) -> str:
        async with httpx.AsyncClient(timeout=60) as download_client:
            source_response = await download_client.get(source_media_url)
            source_response.raise_for_status()

        content_type = source_response.headers.get("content-type", "image/png")
        extension = self._extension_from_content_type(content_type)
        filename = f"vibevision-{uuid4().hex}{extension}"
        files = {"image": (filename, source_response.content, content_type)}
        data = {"overwrite": "true", "type": "input"}
        response = await client.post("/upload/image", data=data, files=files)
        response.raise_for_status()
        body = response.json()
        name = body.get("name") or filename
        subfolder = body.get("subfolder") or ""
        return f"{subfolder}/{name}" if subfolder else name

    def _extension_from_content_type(self, content_type: str) -> str:
        if "jpeg" in content_type or "jpg" in content_type:
            return ".jpg"
        if "webp" in content_type:
            return ".webp"
        if "gif" in content_type:
            return ".gif"
        return ".png"

    def _apply_prompt_values(
        self,
        value: Any,
        prompt: str,
        source_image_name: str | None,
    ) -> Any:
        if isinstance(value, dict):
            return {
                key: self._apply_prompt_values(item, prompt, source_image_name)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._apply_prompt_values(item, prompt, source_image_name) for item in value]
        if value == "__prompt__":
            return prompt
        if value == "__source_image__":
            if not source_image_name:
                raise ValueError("This ComfyUI workflow requires a source image.")
            return source_image_name
        return value

    async def wait_for_result(self, prompt_id: str) -> list[str]:
        deadline = asyncio.get_running_loop().time() + self.settings.comfyui_poll_timeout_seconds

        async with httpx.AsyncClient(base_url=self.settings.comfyui_base_url, timeout=30) as client:
            while asyncio.get_running_loop().time() < deadline:
                response = await client.get(f"/history/{prompt_id}")
                response.raise_for_status()
                result_urls = self._extract_result_urls(response.json(), prompt_id)
                if result_urls:
                    return result_urls
                await asyncio.sleep(self.settings.comfyui_poll_interval_seconds)

        raise TimeoutError(f"ComfyUI job {prompt_id} did not finish before timeout.")

    def _extract_result_urls(self, history: dict[str, Any], prompt_id: str) -> list[str]:
        job = history.get(prompt_id) or next(iter(history.values()), None)
        if not isinstance(job, dict):
            return []

        outputs = job.get("outputs") or {}
        result_urls: list[str] = []
        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue

            for key in ("images", "videos", "gifs"):
                for item in node_output.get(key, []):
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    query = urlencode(
                        {
                            "filename": item["filename"],
                            "subfolder": item.get("subfolder", ""),
                            "type": item.get("type", "output"),
                        }
                    )
                    result_urls.append(f"{self.settings.comfyui_base_url}/view?{query}")

        return result_urls
