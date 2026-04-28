import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

from app.core.config import Settings
from app.models import TaskKind, Workflow


class TargetOutput(StrEnum):
    image = "image"
    video = "video"


class TargetOutputRequiredError(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowRoute:
    workflow_key: str
    kind: TaskKind
    name: str
    description: str
    requires_source_media: bool
    credit_cost: int
    target_output: TargetOutput | None
    is_dispatchable: bool


@dataclass(frozen=True)
class IntentResult:
    workflow_key: str
    kind: TaskKind
    prompt: str
    confidence: float
    reasoning: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


class IntentService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def classify(
        self,
        text: str | None,
        available_workflows: Sequence[Workflow],
        has_image: bool = False,
        source_media_url: str | None = None,
        source_media_type: str | None = None,
        target_output: TargetOutput | str | None = None,
    ) -> IntentResult:
        content = (text or "").strip()
        resolved_target = self.normalize_target_output(target_output)
        workflow_routes = self._build_workflow_routes(available_workflows)
        if resolved_target:
            workflow_routes = [
                route for route in workflow_routes if route.target_output == resolved_target
            ]
        if not workflow_routes:
            raise ValueError("No active workflows are available for intent routing.")

        media_attached = has_image or bool(source_media_url)
        normalized_media_type = self.normalize_media_type(source_media_type)
        images = (
            await self._load_ollama_images(source_media_url)
            if source_media_url and normalized_media_type != "video"
            else []
        )

        if not content and media_attached and not images and not resolved_target:
            workflow = self._pick_workflow(
                workflow_routes,
                preferred_kinds=(TaskKind.prompt_expand, TaskKind.image_edit),
                media_attached=media_attached,
                source_media_type=normalized_media_type,
            )
            return IntentResult(
                workflow_key=workflow.workflow_key,
                kind=workflow.kind,
                prompt=self._default_prompt_for(workflow),
                confidence=0.55,
            )

        try:
            intent = await self._classify_with_ollama(
                text=content,
                workflow_routes=workflow_routes,
                media_attached=media_attached,
                source_media_type=normalized_media_type,
                target_output=resolved_target,
                images=images,
            )
        except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError):
            intent = self._fallback_classify(
                text=content,
                workflow_routes=workflow_routes,
                media_attached=media_attached,
                source_media_type=normalized_media_type,
                target_output=resolved_target,
            )
        return await self._finalize_intent_result(
            intent=intent,
            text=content,
            workflow_routes=workflow_routes,
            media_attached=media_attached,
            source_media_type=normalized_media_type,
            images=images,
        )

    def resolve_target_output(
        self,
        text: str | None,
        explicit_target: TargetOutput | str | None = None,
    ) -> TargetOutput:
        target = self.normalize_target_output(explicit_target)
        if target:
            return target

        normalized = self._normalize_text(text)
        if not normalized:
            raise TargetOutputRequiredError(self._target_required_message())

        image_hit = self._mentions_image_target(normalized)
        video_hit = self._mentions_video_target(normalized)
        if image_hit and not video_hit:
            return TargetOutput.image
        if video_hit and not image_hit:
            return TargetOutput.video

        if normalized.startswith(("照片 ", "图片 ", "image ", "photo ")):
            return TargetOutput.image
        if normalized.startswith(("视频 ", "video ")):
            return TargetOutput.video

        raise TargetOutputRequiredError(self._target_required_message())

    def normalize_target_output(self, value: TargetOutput | str | None) -> TargetOutput | None:
        if isinstance(value, TargetOutput):
            return value
        normalized = self._normalize_text(value)
        if normalized in {"image", "images", "photo", "photos", "picture", "pictures", "图片", "照片", "图"}:
            return TargetOutput.image
        if normalized in {"video", "videos", "movie", "clip", "视频", "短片", "动画"}:
            return TargetOutput.video
        return None

    def normalize_media_type(self, value: str | None) -> str | None:
        normalized = self._normalize_text(value)
        if normalized.startswith("image") or normalized in {"图片", "照片", "photo"}:
            return "image"
        if normalized.startswith("video") or normalized in {"视频", "动画", "animation"}:
            return "video"
        return None

    def workflows_for_target(
        self,
        workflows: Sequence[Workflow],
        *,
        target_output: TargetOutput,
        source_media_type: str | None,
        require_dispatchable: bool = True,
    ) -> list[Workflow]:
        normalized_media_type = self.normalize_media_type(source_media_type)
        result: list[Workflow] = []
        for workflow in workflows:
            route = self._route_for_workflow(workflow)
            if not route or route.target_output != target_output:
                continue
            if require_dispatchable and not route.is_dispatchable:
                continue
            if route.requires_source_media and not normalized_media_type:
                continue
            if workflow.kind == TaskKind.image_edit and normalized_media_type == "video":
                continue
            if workflow.kind == TaskKind.video_image_to_video and normalized_media_type == "video":
                continue
            result.append(workflow)
        return result

    async def _classify_with_ollama(
        self,
        text: str,
        workflow_routes: Sequence[WorkflowRoute],
        media_attached: bool,
        source_media_type: str | None,
        target_output: TargetOutput | None,
        images: list[str],
    ) -> IntentResult:
        logic_model = self.settings.ollama_logic_model_name
        if not logic_model:
            raise ValueError("Ollama logic model is not configured.")

        workflow_catalog = "\n".join(
            (
                f"- workflow_key={route.workflow_key}; kind={route.kind.value}; "
                f"target_output={route.target_output.value if route.target_output else 'none'}; "
                f"requires_source_media={str(route.requires_source_media).lower()}; "
                f"credit_cost={route.credit_cost}; name={route.name}; description={route.description}"
            )
            for route in workflow_routes
        )
        system_prompt = (
            "You are VibeVision's workflow router. Choose exactly one workflow from the catalog, "
            "then draft a concise prompt brief and practical generation parameters. A separate "
            "prompt model will expand the final prompt later. Return compact JSON with keys "
            "workflow_key, prompt, parameters, confidence, reasoning. workflow_key must be copied "
            "exactly from the catalog. Return JSON only.\n"
            "Routing policy:\n"
            "1. Respect target_output. If target_output=image, choose image.generate or image.edit. "
            "If target_output=video, choose video.text_to_video or video.image_to_video.\n"
            "2. If source media is attached and the user wants the same subject changed, edited, "
            "restyled, retouched, or preserved while altering clothing, background, style, objects, "
            "pose, or lighting, choose image.edit for image output.\n"
            "3. If source media is attached and target_output=video, choose video.image_to_video.\n"
            "4. If no source media is attached, choose text-to-media workflows.\n"
            "5. Use visual evidence from attached images when available. Combine it with the user's "
            "text into a faithful prompt brief for the next prompt-writing stage. Do not include "
            "safety disclaimers or chatty text.\n"
            "6. parameters should be a small JSON object, for example width, height, steps, cfg, "
            "duration_seconds, fps, motion_strength, seed, negative_prompt. Use only values that "
            "fit the selected workflow and the user's request."
        )
        payload: dict[str, Any] = {
            "model": logic_model,
            "stream": False,
            "format": "json",
            "prompt": (
                f"{system_prompt}\n\n"
                f"workflow_catalog:\n{workflow_catalog}\n\n"
                f"target_output={target_output.value if target_output else 'unspecified'}\n"
                f"media_attached={media_attached}\n"
                f"source_media_type={source_media_type or 'none'}\n"
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
        workflow = self._resolve_workflow_choice(
            data,
            workflow_routes,
            media_attached=media_attached,
            source_media_type=source_media_type,
        )
        workflow = self._reconcile_workflow_choice(
            workflow=workflow,
            text=text,
            workflow_routes=workflow_routes,
            media_attached=media_attached,
            source_media_type=source_media_type,
            target_output=target_output,
        )
        return IntentResult(
            workflow_key=workflow.workflow_key,
            kind=workflow.kind,
            prompt=self._clean_prompt_text(str(data.get("prompt") or ""))
            or text
            or self._default_prompt_for(workflow),
            confidence=float(data.get("confidence", 0.7)),
            reasoning=str(data.get("reasoning") or "") or None,
            parameters=self._coerce_parameters(data.get("parameters")),
        )

    async def _finalize_intent_result(
        self,
        *,
        intent: IntentResult,
        text: str,
        workflow_routes: Sequence[WorkflowRoute],
        media_attached: bool,
        source_media_type: str | None,
        images: list[str],
    ) -> IntentResult:
        workflow = next(
            (route for route in workflow_routes if route.workflow_key == intent.workflow_key),
            None,
        )
        if not workflow:
            return intent
        prompt = await self._finalize_prompt(
            workflow=workflow,
            user_text=text,
            router_prompt=intent.prompt,
            media_attached=media_attached,
            source_media_type=source_media_type,
            images=images,
        )
        return IntentResult(
            workflow_key=intent.workflow_key,
            kind=intent.kind,
            prompt=prompt,
            confidence=intent.confidence,
            reasoning=intent.reasoning,
            parameters=intent.parameters,
        )

    async def _finalize_prompt(
        self,
        *,
        workflow: WorkflowRoute,
        user_text: str,
        router_prompt: str,
        media_attached: bool,
        source_media_type: str | None,
        images: list[str],
    ) -> str:
        base_prompt = (
            self._clean_prompt_text(router_prompt)
            or self._clean_prompt_text(user_text)
            or self._default_prompt_for(workflow)
        )
        if not self._should_enhance_prompt(workflow, user_text):
            return base_prompt

        try:
            enhanced_prompt = await self._enhance_prompt_with_ollama(
                workflow=workflow,
                user_text=user_text,
                router_prompt=base_prompt,
                media_attached=media_attached,
                source_media_type=source_media_type,
                images=images,
            )
        except (httpx.HTTPError, ValueError):
            return base_prompt
        return self._limit_prompt_text(self._clean_prompt_text(enhanced_prompt)) or base_prompt

    async def _enhance_prompt_with_ollama(
        self,
        *,
        workflow: WorkflowRoute,
        user_text: str,
        router_prompt: str,
        media_attached: bool,
        source_media_type: str | None,
        images: list[str],
    ) -> str:
        prompt_model = self.settings.ollama_prompt_model_name
        if not prompt_model:
            raise ValueError("Ollama prompt model is not configured.")

        system_prompt = (
            "You are VibeVision's controlled prompt enhancement model. Rewrite the user's request "
            "into a concise production prompt for the selected generation workflow.\n"
            "Rules:\n"
            "1. Make only necessary, appropriate expansion. Do not turn a simple edit into a new "
            "scene concept.\n"
            "2. Output one compact sentence, or two short sentences if needed. Keep it under "
            "70 English words or 240 Chinese characters.\n"
            "3. If source media is attached, use only key visible facts from the image: subject, "
            "pose/composition, and existing environment. Preserve identity, pose, framing, "
            "background, and lighting unless the user explicitly asks to change them.\n"
            "4. For image editing, combine the user's requested change with 2-4 essential source "
            "image details. Do not list every detail. Do not add new locations, props, styles, "
            "camera moves, moods, or lighting unless requested.\n"
            "5. If workflow_note says English is required, write the final prompt in English even "
            "when the user request is in another language.\n"
            "6. Return only the final prompt text. No bullets, no JSON, no explanation."
        )
        payload: dict[str, Any] = {
            "model": prompt_model,
            "stream": False,
            "prompt": (
                f"{system_prompt}\n\n"
                f"workflow_kind={workflow.kind.value}\n"
                f"target_output={workflow.target_output.value if workflow.target_output else 'none'}\n"
                f"media_attached={media_attached}\n"
                f"source_media_type={source_media_type or 'none'}\n"
                f"workflow_note={self._prompt_enhancement_note_for(workflow)}\n"
                f"router_prompt_brief={self._clean_prompt_text(router_prompt) or 'none'}\n"
                f"user_request={self._clean_prompt_text(user_text) or 'none'}\n"
                "Final prompt:"
            ),
        }
        if images:
            payload["images"] = images

        async with httpx.AsyncClient(base_url=self.settings.ollama_base_url, timeout=60) as client:
            response = await client.post("/api/generate", json=payload)
            response.raise_for_status()
            body = response.json()
        return str(body.get("response") or "").strip()

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

    def _fallback_classify(
        self,
        text: str,
        workflow_routes: Sequence[WorkflowRoute],
        media_attached: bool,
        source_media_type: str | None = None,
        target_output: TargetOutput | None = None,
    ) -> IntentResult:
        preferred_kinds = self._preferred_kinds_for(
            text,
            media_attached=media_attached,
            source_media_type=source_media_type,
            target_output=target_output,
        )
        workflow = self._pick_workflow(
            workflow_routes,
            preferred_kinds=preferred_kinds,
            media_attached=media_attached,
            source_media_type=source_media_type,
        )
        return IntentResult(
            workflow_key=workflow.workflow_key,
            kind=workflow.kind,
            prompt=text or self._default_prompt_for(workflow),
            confidence=0.35,
            parameters=self._default_parameters_for(workflow),
        )

    def _build_workflow_routes(
        self,
        available_workflows: Sequence[Workflow],
    ) -> list[WorkflowRoute]:
        routes: list[WorkflowRoute] = []
        for workflow in available_workflows:
            route = self._route_for_workflow(workflow)
            if route:
                routes.append(route)
        return routes

    def _route_for_workflow(self, workflow: Workflow) -> WorkflowRoute | None:
        if not workflow.is_active:
            return None
        return WorkflowRoute(
            workflow_key=workflow.comfy_workflow_key,
            kind=workflow.kind,
            name=workflow.name,
            description=workflow.description or "No description provided.",
            requires_source_media=self._workflow_requires_source_media(workflow.kind),
            credit_cost=workflow.credit_cost,
            target_output=self._target_output_for_workflow(workflow.kind),
            is_dispatchable=self.workflow_is_dispatchable(workflow),
        )

    def workflow_is_dispatchable(self, workflow: Workflow) -> bool:
        template = workflow.template or {}
        if not isinstance(template, dict) or not template:
            return False
        if isinstance(template.get("prompt"), dict):
            return True
        return self._looks_like_prompt_graph(template)

    def _resolve_workflow_choice(
        self,
        data: dict,
        workflow_routes: Sequence[WorkflowRoute],
        media_attached: bool,
        source_media_type: str | None,
    ) -> WorkflowRoute:
        workflow_key = str(data.get("workflow_key") or "").strip()
        if workflow_key:
            workflow = next(
                (route for route in workflow_routes if route.workflow_key == workflow_key),
                None,
            )
            if workflow:
                return workflow

        raw_kind = data.get("kind")
        if raw_kind:
            kind = TaskKind(raw_kind)
            return self._pick_workflow(
                workflow_routes,
                preferred_kinds=(kind,),
                media_attached=media_attached,
                source_media_type=source_media_type,
            )

        raise ValueError("Ollama returned an unknown workflow.")

    def _reconcile_workflow_choice(
        self,
        workflow: WorkflowRoute,
        text: str,
        workflow_routes: Sequence[WorkflowRoute],
        media_attached: bool,
        source_media_type: str | None = None,
        target_output: TargetOutput | None = None,
    ) -> WorkflowRoute:
        if target_output and workflow.target_output != target_output:
            return self._pick_workflow(
                workflow_routes,
                preferred_kinds=self._preferred_kinds_for(
                    text,
                    media_attached=media_attached,
                    source_media_type=source_media_type,
                    target_output=target_output,
                ),
                media_attached=media_attached,
                source_media_type=source_media_type,
            )

        preferred_kinds = self._preferred_kinds_for(
            text,
            media_attached=media_attached,
            source_media_type=source_media_type,
            target_output=target_output,
        )
        preferred_kind = preferred_kinds[0]
        if preferred_kind in {
            TaskKind.image_edit,
            TaskKind.video_image_to_video,
            TaskKind.video_text_to_video,
        }:
            return self._pick_workflow(
                workflow_routes,
                preferred_kinds=preferred_kinds,
                media_attached=media_attached,
                source_media_type=source_media_type,
            )
        return workflow

    def _pick_workflow(
        self,
        workflow_routes: Sequence[WorkflowRoute],
        preferred_kinds: Sequence[TaskKind],
        media_attached: bool,
        source_media_type: str | None = None,
    ) -> WorkflowRoute:
        normalized_media_type = self.normalize_media_type(source_media_type)
        for kind in preferred_kinds:
            direct_match = next(
                (
                    route
                    for route in workflow_routes
                    if route.kind == kind
                    and (not route.requires_source_media or media_attached)
                    and not (route.kind == TaskKind.image_edit and normalized_media_type == "video")
                ),
                None,
            )
            if direct_match:
                return direct_match

        if media_attached:
            media_capable = next(
                (
                    route
                    for route in workflow_routes
                    if route.requires_source_media
                    and not (route.kind == TaskKind.image_edit and normalized_media_type == "video")
                ),
                None,
            )
            if media_capable:
                return media_capable

        general_match = next(
            (
                route
                for route in workflow_routes
                if route.kind in {TaskKind.image_generate, TaskKind.video_text_to_video}
            ),
            None,
        )
        if general_match:
            return general_match
        return workflow_routes[0]

    def _default_prompt_for(self, workflow: WorkflowRoute) -> str:
        if workflow.kind == TaskKind.image_edit:
            return "Edit the uploaded image according to the user's request."
        if workflow.kind == TaskKind.video_text_to_video:
            return "Create a short video from the user's request."
        if workflow.kind == TaskKind.video_image_to_video:
            return "Animate the uploaded image into a short video sequence."
        if workflow.kind == TaskKind.prompt_expand:
            return "Describe the uploaded media and suggest a detailed generation prompt."
        return "Create a polished image from the user request."

    def _default_parameters_for(self, workflow: WorkflowRoute) -> dict[str, Any]:
        if workflow.target_output == TargetOutput.video:
            return {"duration_seconds": 4, "fps": 16, "motion_strength": 0.45}
        return {"steps": 24, "cfg": 3.5}

    def _should_enhance_prompt(self, workflow: WorkflowRoute, text: str) -> bool:
        return workflow.kind in {
            TaskKind.image_generate,
            TaskKind.image_edit,
            TaskKind.video_text_to_video,
            TaskKind.video_image_to_video,
        } and bool(self._normalize_text(text))

    def _workflow_requires_source_media(self, kind: TaskKind) -> bool:
        return kind in {TaskKind.image_edit, TaskKind.video_image_to_video}

    def _prompt_enhancement_note_for(self, workflow: WorkflowRoute) -> str:
        if workflow.kind == TaskKind.image_edit:
            language_note = (
                " The flux2klein-single-edit workflow only accepts English prompts; write the "
                "final prompt in natural, concise English."
                if workflow.workflow_key == "flux2klein-single-edit"
                else ""
            )
            return (
                "This is an image editing prompt. Keep it brief and instruction-focused: preserve "
                "the uploaded image's identity, pose, framing, background, and lighting, then apply "
                "only the requested edit."
                f"{language_note}"
            )
        if workflow.kind == TaskKind.video_text_to_video:
            return (
                "This is a text-to-video prompt. Keep motion directions clear, cinematic, and "
                "physically plausible without inventing new events."
            )
        if workflow.kind == TaskKind.video_image_to_video:
            return (
                "This is an image-to-video prompt. Preserve the original identity and composition, "
                "and only add motion or temporal detail that supports the request."
            )
        return (
            "This is a text-to-image prompt. Strengthen visual specificity and image quality while "
            "keeping the requested subject and style unchanged."
        )

    def _target_output_for_workflow(self, kind: TaskKind) -> TargetOutput | None:
        if kind in {TaskKind.image_generate, TaskKind.image_edit}:
            return TargetOutput.image
        if kind in {TaskKind.video_text_to_video, TaskKind.video_image_to_video}:
            return TargetOutput.video
        return None

    def _preferred_kinds_for(
        self,
        text: str,
        media_attached: bool,
        source_media_type: str | None = None,
        target_output: TargetOutput | None = None,
    ) -> tuple[TaskKind, ...]:
        lowered = self._normalize_text(text)
        normalized_media_type = self.normalize_media_type(source_media_type)

        if target_output == TargetOutput.video:
            if media_attached and normalized_media_type != "video":
                return (TaskKind.video_image_to_video, TaskKind.video_text_to_video)
            return (TaskKind.video_text_to_video, TaskKind.video_image_to_video)

        if target_output == TargetOutput.image:
            if media_attached and self._mentions_edit_intent(lowered):
                return (TaskKind.image_edit, TaskKind.image_generate)
            if media_attached:
                return (TaskKind.image_edit, TaskKind.image_generate)
            return (TaskKind.image_generate, TaskKind.image_edit)

        if media_attached and any(
            token in lowered for token in ["video", "motion", "animate", "动", "视频"]
        ):
            return (TaskKind.video_image_to_video, TaskKind.image_edit)
        if media_attached and self._mentions_edit_intent(lowered):
            return (TaskKind.image_edit, TaskKind.prompt_expand)
        if any(token in lowered for token in ["prompt", "提示词", "扩写", "理解图片", "describe"]):
            return (TaskKind.prompt_expand, TaskKind.image_generate)
        if media_attached and not lowered:
            return (TaskKind.prompt_expand, TaskKind.image_edit)
        return (TaskKind.image_generate, TaskKind.prompt_expand)

    def _coerce_parameters(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, Any] = {}
        for key, item in value.items():
            clean_key = str(key).strip()
            if not clean_key:
                continue
            if isinstance(item, str | int | float | bool) or item is None:
                result[clean_key] = item
            elif isinstance(item, list):
                result[clean_key] = [
                    entry for entry in item if isinstance(entry, str | int | float | bool)
                ][:10]
        return result

    def _looks_like_prompt_graph(self, value: dict[str, Any]) -> bool:
        return bool(value) and all(
            isinstance(node, dict) and isinstance(node.get("class_type"), str)
            for node in value.values()
        )

    def _mentions_image_target(self, normalized: str) -> bool:
        return any(
            token in normalized
            for token in [
                "图片",
                "照片",
                "图像",
                "文生图",
                "生图",
                "出图",
                "修图",
                "p图",
                "改图",
                "改照片",
                "image",
                "photo",
                "picture",
            ]
        )

    def _mentions_video_target(self, normalized: str) -> bool:
        return any(
            token in normalized
            for token in [
                "视频",
                "短片",
                "动画",
                "动起来",
                "运镜",
                "文生视频",
                "图生视频",
                "生视频",
                "video",
                "movie",
                "clip",
                "animate",
                "motion",
            ]
        )

    def _mentions_edit_intent(self, normalized: str) -> bool:
        return any(
            token in normalized
            for token in [
                "edit",
                "change",
                "replace",
                "restyle",
                "outfit",
                "clothing",
                "dress",
                "background",
                "修改",
                "编辑",
                "改图",
                "改照片",
                "改",
                "改成",
                "变成",
                "替换",
                "换",
                "换个",
                "换掉",
                "脱",
                "去掉",
                "增加",
                "添加",
                "背景",
                "衣服",
            ]
        )

    def _target_required_message(self) -> str:
        return (
            "请先明确选择要生成图片还是视频。"
            "可以直接输入 /photo 或 /p 加描述，或输入 /video 或 /v 加描述；"
            "也可以在描述里直接写“生成图片...”或“生成视频...”。"
        )

    def _clean_prompt_text(self, text: str | None) -> str:
        normalized = " ".join((text or "").strip().split())
        if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
            normalized = normalized[1:-1].strip()
        return normalized

    def _limit_prompt_text(self, text: str) -> str:
        if len(text) <= 600:
            return text
        return text[:600].rsplit(" ", 1)[0].rstrip("，,。.") or text[:600].rstrip()

    def _normalize_text(self, text: str | None) -> str:
        return " ".join((text or "").strip().lower().split())
