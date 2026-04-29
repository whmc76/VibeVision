import asyncio
import copy
import random
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from app.core.config import Settings
from app.models import Workflow
from app.services.concurrency import concurrency_slot


class ComfyUIClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def submit_prompt(
        self,
        workflow: Workflow,
        prompt: str,
        source_media_url: str | None,
        parameters: dict[str, Any] | None = None,
    ) -> str:
        async with concurrency_slot("comfyui", self.settings.comfyui_max_concurrency):
            async with httpx.AsyncClient(
                base_url=self.settings.comfyui_base_url,
                timeout=30,
                limits=self._single_connection_limits(),
            ) as client:
                source_image_name = None
                if source_media_url and self._template_requires_source_image(workflow.template or {}):
                    source_image_name = await self._upload_source_image(client, source_media_url)

                payload = self._build_payload(workflow, prompt, source_image_name, parameters or {})
                response = await client.post("/prompt", json=payload)
                response.raise_for_status()
                data = response.json()
        return str(data.get("prompt_id") or data.get("id") or uuid4())

    def _build_payload(
        self,
        workflow: Workflow,
        prompt: str,
        source_image_name: str | None,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        template = workflow.template or {}
        if template.get("prompt"):
            prompt_graph = self._apply_prompt_values(
                copy.deepcopy(template["prompt"]),
                prompt=prompt,
                source_image_name=source_image_name,
                parameters=parameters,
            )
            return {"client_id": "vibevision-api", "prompt": prompt_graph}

        if self._looks_like_prompt_graph(template):
            prompt_graph = self._apply_prompt_values(
                copy.deepcopy(template),
                prompt=prompt,
                source_image_name=source_image_name,
                parameters=parameters,
            )
            return {"client_id": "vibevision-api", "prompt": prompt_graph}

        raise ValueError(
            f"Workflow {workflow.comfy_workflow_key} does not contain a valid ComfyUI API prompt graph."
        )

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
        parameters: dict[str, Any],
    ) -> Any:
        if isinstance(value, dict):
            return {
                key: self._apply_prompt_values(item, prompt, source_image_name, parameters)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                self._apply_prompt_values(item, prompt, source_image_name, parameters)
                for item in value
            ]
        if value == "__prompt__":
            return prompt
        if value == "__source_image__":
            if not source_image_name:
                raise ValueError("This ComfyUI workflow requires a source image.")
            return source_image_name
        if value == "__random_seed__":
            return random.randint(0, 2**63 - 1)
        if isinstance(value, str) and value.startswith("__param:") and value.endswith("__"):
            key = value.removeprefix("__param:").removesuffix("__")
            if key not in parameters:
                raise ValueError(f"Workflow parameter {key!r} was not provided.")
            return parameters[key]
        return value

    def _looks_like_prompt_graph(self, value: dict[str, Any]) -> bool:
        return bool(value) and all(
            isinstance(node, dict) and isinstance(node.get("class_type"), str)
            for node in value.values()
        )

    def _template_requires_source_image(self, value: Any) -> bool:
        if isinstance(value, dict):
            return any(self._template_requires_source_image(item) for item in value.values())
        if isinstance(value, list):
            return any(self._template_requires_source_image(item) for item in value)
        return value == "__source_image__"

    async def wait_for_result(self, prompt_id: str) -> list[str]:
        deadline = asyncio.get_running_loop().time() + self.settings.comfyui_poll_timeout_seconds

        async with concurrency_slot("comfyui", self.settings.comfyui_max_concurrency):
            async with httpx.AsyncClient(
                base_url=self.settings.comfyui_base_url,
                timeout=30,
                limits=self._single_connection_limits(),
            ) as client:
                while asyncio.get_running_loop().time() < deadline:
                    result_urls = await self._get_result_urls(client, prompt_id)
                    if result_urls:
                        return result_urls
                    await asyncio.sleep(self.settings.comfyui_poll_interval_seconds)

        raise TimeoutError(f"ComfyUI job {prompt_id} did not finish before timeout.")

    async def get_result_urls(self, prompt_id: str) -> list[str]:
        async with concurrency_slot("comfyui", self.settings.comfyui_max_concurrency):
            async with httpx.AsyncClient(
                base_url=self.settings.comfyui_base_url,
                timeout=30,
                limits=self._single_connection_limits(),
            ) as client:
                return await self._get_result_urls(client, prompt_id)

    async def _get_result_urls(self, client: httpx.AsyncClient, prompt_id: str) -> list[str]:
        response = await client.get(f"/history/{prompt_id}")
        response.raise_for_status()
        return self._extract_result_urls(response.json(), prompt_id)

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

    def _single_connection_limits(self) -> httpx.Limits:
        return httpx.Limits(max_connections=1, max_keepalive_connections=1)
