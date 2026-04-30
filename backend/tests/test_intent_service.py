import asyncio
import logging

import httpx

from app.core.config import Settings
from app.models import TaskKind, Workflow
from app.services.intent import IntentResult, IntentService


def build_workflows() -> list[Workflow]:
    return [
        Workflow(
            name="Z-Image Turbo Text To Image",
            kind=TaskKind.image_generate,
            comfy_workflow_key="z-image-turbo-text-to-image",
            description="Generate images from text prompts.",
            credit_cost=6,
            is_active=True,
        ),
        Workflow(
            name="Image Edit Assistant",
            kind=TaskKind.image_edit,
            comfy_workflow_key="flux2klein-single-edit",
            description="Edit uploaded images.",
            credit_cost=8,
            is_active=True,
        ),
        Workflow(
            name="Image To Video Motion",
            kind=TaskKind.video_image_to_video,
            comfy_workflow_key="image-to-video",
            description="Animate user-provided images into short videos.",
            credit_cost=18,
            is_active=True,
        ),
        Workflow(
            name="Image Understanding Prompt Writer",
            kind=TaskKind.prompt_expand,
            comfy_workflow_key="prompt-expand",
            description="Understand media and expand user intent into prompts.",
            credit_cost=2,
            is_active=True,
        ),
    ]


def dispatchable_template() -> dict:
    return {
        "prompt": {
            "1": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "__prompt__"},
            }
        }
    }


def build_service() -> IntentService:
    return IntentService(Settings(llm_provider="ollama"))


def test_resolve_target_output_requires_image_or_video_choice() -> None:
    service = build_service()

    try:
        service.resolve_target_output("给她换一套衣服")
    except ValueError as exc:
        assert "图片还是视频" in str(exc)
    else:
        raise AssertionError("Expected target output selection to be required.")

    assert service.resolve_target_output("生成图片，给她换一套衣服").value == "image"
    assert service.resolve_target_output("生成视频，让这张图动起来").value == "video"
    assert service.resolve_target_output("生图，一只猫").value == "image"
    assert service.resolve_target_output("生视频，海边日落运镜").value == "video"
    assert service.resolve_target_output("改图，把背景换成海边").value == "image"
    assert service.resolve_target_output("改照片，保留人物换背景").value == "image"
    assert service.resolve_target_output("图片编辑，把背景换成海边").value == "image"
    assert service.resolve_target_output("编辑图，保留人物换背景").value == "image"
    assert service.resolve_target_output("generate image, a cat").value == "image"
    assert service.resolve_target_output("edit image, change background").value == "image"
    assert service.resolve_target_output("retouch photo, fix skin naturally").value == "image"
    assert service.resolve_target_output("generate video, ocean sunset").value == "video"
    assert service.resolve_target_output("text to video, ocean sunset").value == "video"
    assert service.resolve_target_output("image to video, slow push-in").value == "video"


def test_workflows_for_target_filters_empty_templates_and_media_type() -> None:
    service = build_service()
    workflows = build_workflows()
    workflows[0].template = dispatchable_template()
    workflows[1].template = dispatchable_template()
    workflows[2].template = {}

    text_to_image = service.workflows_for_target(
        workflows,
        target_output=service.resolve_target_output("生成图片"),
        source_media_type=None,
    )
    image_edit_candidates = service.workflows_for_target(
        workflows,
        target_output=service.resolve_target_output("生成图片"),
        source_media_type="image",
    )
    video_candidates = service.workflows_for_target(
        workflows,
        target_output=service.resolve_target_output("生成视频"),
        source_media_type="image",
    )

    assert [workflow.kind for workflow in text_to_image] == [TaskKind.image_generate]
    assert [workflow.kind for workflow in image_edit_candidates] == [
        TaskKind.image_generate,
        TaskKind.image_edit,
    ]
    assert video_candidates == []


def test_fallback_selects_image_generate_for_plain_text() -> None:
    service = build_service()
    routes = service._build_workflow_routes(build_workflows())

    result = service._fallback_classify(
        text="cinematic portrait with soft rim light",
        workflow_routes=routes,
        media_attached=False,
    )

    assert result.workflow_key == "z-image-turbo-text-to-image"
    assert result.kind == TaskKind.image_generate


def test_fallback_selects_edit_workflow_for_image_edit_request() -> None:
    service = build_service()
    routes = service._build_workflow_routes(build_workflows())

    result = service._fallback_classify(
        text="把背景改成高级灰展厅",
        workflow_routes=routes,
        media_attached=True,
    )

    assert result.workflow_key == "flux2klein-single-edit"
    assert result.kind == TaskKind.image_edit


def test_fallback_selects_edit_workflow_for_short_chinese_edit_keywords() -> None:
    service = build_service()
    routes = service._build_workflow_routes(build_workflows())

    for text in [
        "改图，把背景换成海边",
        "改照片，保留人物换背景",
        "修图，把皮肤修自然一点",
        "图片编辑，把背景换成海边",
        "编辑图，保留人物换背景",
        "编辑图片，加暖色电影感",
        "edit image, change background to beach",
        "image edit, change background to beach",
        "retouch photo, fix skin naturally",
        "enhance picture, add cinematic lighting",
    ]:
        result = service._fallback_classify(
            text=text,
            workflow_routes=routes,
            media_attached=True,
            target_output=service.resolve_target_output(text),
        )

        assert result.workflow_key == "flux2klein-single-edit"
        assert result.kind == TaskKind.image_edit


def test_fallback_selects_edit_workflow_for_change_clothes_request() -> None:
    service = build_service()
    routes = service._build_workflow_routes(build_workflows())

    result = service._fallback_classify(
        text="给她换个性感泳装",
        workflow_routes=routes,
        media_attached=True,
    )

    assert result.workflow_key == "flux2klein-single-edit"
    assert result.kind == TaskKind.image_edit


def test_reconcile_overrides_text_to_image_for_attached_image_edit_request() -> None:
    service = build_service()
    routes = service._build_workflow_routes(build_workflows())
    text_to_image = next(route for route in routes if route.kind == TaskKind.image_generate)

    result = service._reconcile_workflow_choice(
        workflow=text_to_image,
        text="给她换个性感泳装",
        workflow_routes=routes,
        media_attached=True,
    )

    assert result.workflow_key == "flux2klein-single-edit"
    assert result.kind == TaskKind.image_edit


def test_fallback_uses_edit_workflow_for_motion_request_without_video_routes() -> None:
    service = build_service()
    routes = service._build_workflow_routes(build_workflows())

    result = service._fallback_classify(
        text="animate this image with a slow push-in",
        workflow_routes=routes,
        media_attached=True,
    )

    assert result.workflow_key == "flux2klein-single-edit"
    assert result.kind == TaskKind.image_edit


def test_classify_rejects_retired_video_routes(monkeypatch) -> None:
    service = build_service()

    async def fake_load_visual_inputs(**kwargs) -> tuple[list[str], str]:
        return [], ""

    monkeypatch.setattr(service, "_load_visual_inputs", fake_load_visual_inputs)

    try:
        asyncio.run(
            service.classify(
                text="生成视频，让镜头慢慢推进",
                available_workflows=build_workflows(),
                has_image=True,
                source_media_type="image",
                target_output=service.resolve_target_output("生成视频"),
            )
        )
    except ValueError as exc:
        assert "No active workflows" in str(exc)
    else:
        raise AssertionError("Expected retired video routes to be unavailable.")


def test_fallback_selects_image_generate_for_prompt_help_without_prompt_expand() -> None:
    service = build_service()
    routes = service._build_workflow_routes(build_workflows())

    result = service._fallback_classify(
        text="帮我扩写提示词",
        workflow_routes=routes,
        media_attached=False,
    )

    assert result.workflow_key == "z-image-turbo-text-to-image"
    assert result.kind == TaskKind.image_generate


def test_classify_routes_empty_media_request_to_image_edit(monkeypatch) -> None:
    service = build_service()

    async def fake_load_visual_inputs(**kwargs) -> tuple[list[str], str]:
        return [], ""

    monkeypatch.setattr(service, "_load_visual_inputs", fake_load_visual_inputs)

    result = asyncio.run(
        service.classify(
            text=None,
            available_workflows=build_workflows(),
            has_image=True,
            source_media_url="https://example.com/source.png",
        )
    )

    assert result.workflow_key == "flux2klein-single-edit"
    assert result.kind == TaskKind.image_edit


def test_classify_enhances_generation_prompt_after_fallback(monkeypatch) -> None:
    service = build_service()

    async def fake_classify_with_llm(**kwargs) -> object:
        raise httpx.ConnectError("router offline")

    async def fake_enhance_prompt_with_llm(**kwargs) -> str:
        return "cinematic portrait of a silver cat, soft rim light, shallow depth of field"

    monkeypatch.setattr(service, "_classify_with_llm", fake_classify_with_llm)
    monkeypatch.setattr(service, "_enhance_prompt_with_llm", fake_enhance_prompt_with_llm)

    result = asyncio.run(
        service.classify(
            text="生成图片，一只银渐层猫",
            available_workflows=build_workflows(),
            target_output=service.resolve_target_output("生成图片"),
        )
    )

    assert result.workflow_key == "z-image-turbo-text-to-image"
    assert result.kind == TaskKind.image_generate
    assert result.prompt == (
        "cinematic portrait of a silver cat, soft rim light, shallow depth of field"
    )


def test_klein_edit_prompt_enhancement_requires_english_note() -> None:
    service = build_service()
    route = service._build_workflow_routes(build_workflows())[1]

    note = service._prompt_enhancement_note_for(route)

    assert route.workflow_key == "flux2klein-single-edit"
    assert "English prompts" in note


def test_llm_json_parser_ignores_thinking_tags() -> None:
    service = build_service()

    data = service._loads_json_object(
        '<think>pick a workflow</think>{"workflow_key":"z-image-turbo-text-to-image","prompt":"cat"}'
    )

    assert data == {"workflow_key": "z-image-turbo-text-to-image", "prompt": "cat"}


def test_logic_and_prompt_providers_can_be_split(monkeypatch) -> None:
    service = IntentService(
        Settings(
            llm_provider="minimax",
            llm_logic_provider="minimax",
            llm_prompt_provider="ollama",
            minimax_api_key="test-key",
            ollama_prompt_model="qwen-prompt-model",
        )
    )
    calls: list[str] = []

    async def fake_classify_with_minimax(**kwargs) -> IntentResult:
        calls.append("minimax-logic")
        return IntentResult(
            workflow_key="z-image-turbo-text-to-image",
            kind=TaskKind.image_generate,
            prompt="router brief",
            confidence=0.8,
        )

    async def fake_enhance_prompt_with_ollama(**kwargs) -> str:
        calls.append("ollama-prompt")
        return "enhanced prompt"

    monkeypatch.setattr(service, "_classify_with_minimax", fake_classify_with_minimax)
    monkeypatch.setattr(service, "_enhance_prompt_with_ollama", fake_enhance_prompt_with_ollama)

    result = asyncio.run(
        service.classify(
            text="生成图片，一只猫",
            available_workflows=build_workflows(),
            target_output=service.resolve_target_output("生成图片"),
        )
    )

    assert result.prompt == "enhanced prompt"
    assert calls == ["minimax-logic", "ollama-prompt"]


def test_enhance_task_prompt_passes_source_image_context(monkeypatch) -> None:
    service = build_service()
    workflow = build_workflows()[1]

    async def fake_load_visual_inputs(**kwargs) -> tuple[list[str], str]:
        assert kwargs["source_media_url"] == "https://example.test/source.png"
        assert kwargs["source_media_type"] == "image"
        assert kwargs["user_text"] == "改图，换成电影感灯光"
        return [], "A person seated indoors, waist-up composition, warm room lighting."

    async def fake_enhance_prompt_with_llm(**kwargs) -> str:
        assert kwargs["vision_context"] == (
            "A person seated indoors, waist-up composition, warm room lighting."
        )
        return "cinematic lighting edit grounded in the source image"

    monkeypatch.setattr(service, "_load_visual_inputs", fake_load_visual_inputs)
    monkeypatch.setattr(service, "_enhance_prompt_with_llm", fake_enhance_prompt_with_llm)

    result = asyncio.run(
        service.enhance_task_prompt(
            workflow=workflow,
            user_text="改图，换成电影感灯光",
            router_prompt="apply cinematic lighting",
            source_media_url="https://example.test/source.png",
            source_media_type="image",
        )
    )

    assert result == "cinematic lighting edit grounded in the source image"


def test_router_prompt_includes_vision_context() -> None:
    service = build_service()
    route = service._build_workflow_routes(build_workflows())[0]

    prompt = service._router_user_prompt(
        text="生成图片",
        workflow_routes=[route],
        media_attached=True,
        source_media_type="image",
        target_output=service.resolve_target_output("生成图片"),
        vision_image_attached=False,
        vision_context="A woman in a red dress standing by a window.",
    )

    assert "vision_context=A woman in a red dress standing by a window." in prompt


def test_minimax_logic_uses_configured_openai_compatible_request(monkeypatch) -> None:
    service = IntentService(
        Settings(
            llm_provider="minimax",
            minimax_base_url="https://api.minimaxi.com/v1/",
            minimax_api_key="test-key",
            minimax_logic_model="MiniMax-M2.7",
            minimax_timeout_seconds=7,
        )
    )
    routes = service._build_workflow_routes(build_workflows())
    captures: list[dict] = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captures.append({"init": kwargs})
            self.base_url = str(kwargs["base_url"]).rstrip("/")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, path: str, json: dict):
            captures.append({"path": path, "json": json})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"workflow_key":"z-image-turbo-text-to-image",'
                                    '"prompt":"cinematic cat","confidence":0.91}'
                                )
                            }
                        }
                    ]
                },
                request=httpx.Request("POST", f"{self.base_url}{path}"),
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        service._classify_with_minimax(
            text="生成图片，一只猫",
            workflow_routes=routes,
            media_attached=False,
            source_media_type=None,
            target_output=service.resolve_target_output("生成图片"),
            vision_context="",
        )
    )

    init = captures[0]["init"]
    request = captures[1]
    assert init["base_url"] == "https://api.minimaxi.com/v1"
    assert init["headers"]["Authorization"] == "Bearer test-key"
    assert init["headers"]["Content-Type"] == "application/json"
    assert init["trust_env"] is False
    assert isinstance(init["timeout"], httpx.Timeout)
    assert request["path"] == "/chat/completions"
    assert request["json"]["model"] == "MiniMax-M2.7"
    assert request["json"]["response_format"] == {"type": "json_object"}
    assert result.workflow_key == "z-image-turbo-text-to-image"
    assert result.prompt == "cinematic cat"


def test_minimax_mcp_vision_uses_coding_plan_vlm_request(monkeypatch) -> None:
    service = IntentService(
        Settings(
            llm_vision_provider="minimax_mcp",
            minimax_api_host="https://api.minimaxi.com/v1",
            minimax_api_key="test-key",
            minimax_vision_timeout_seconds=9,
        )
    )
    captures: list[dict] = []

    async def fake_load_data_url(source_media_url: str) -> str:
        assert source_media_url == "https://example.test/source.png"
        return "data:image/png;base64,abc123"

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captures.append({"init": kwargs})
            self.base_url = str(kwargs["base_url"]).rstrip("/")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, path: str, json: dict):
            captures.append({"path": path, "json": json})
            return httpx.Response(
                200,
                json={"content": "A tabby cat on a windowsill."},
                request=httpx.Request("POST", f"{self.base_url}{path}"),
            )

    monkeypatch.setattr(service, "_load_minimax_image_data_url", fake_load_data_url)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        service._understand_image_with_minimax_mcp(
            source_media_url="https://example.test/source.png",
            user_text="生成图片",
        )
    )

    init = captures[0]["init"]
    request = captures[1]
    assert init["base_url"] == "https://api.minimaxi.com"
    assert init["headers"]["Authorization"] == "Bearer test-key"
    assert init["headers"]["Content-Type"] == "application/json"
    assert init["headers"]["MM-API-Source"] == "Minimax-MCP"
    assert init["trust_env"] is False
    assert isinstance(init["timeout"], httpx.Timeout)
    assert request["path"] == "/v1/coding_plan/vlm"
    assert request["json"]["image_url"] == "data:image/png;base64,abc123"
    assert request["json"]["prompt"].startswith("Analyze this image")
    assert result == "A tabby cat on a windowsill."


def test_minimax_mcp_vision_failure_logs_and_keeps_flow_moving(monkeypatch, caplog) -> None:
    service = IntentService(
        Settings(
            llm_vision_provider="minimax_mcp",
            minimax_api_key="test-key",
        )
    )

    async def fake_understand_image(**kwargs) -> str:
        raise httpx.ConnectError("vlm offline")

    monkeypatch.setattr(service, "_understand_image_with_minimax_mcp", fake_understand_image)

    with caplog.at_level(logging.WARNING):
        images, vision_context = asyncio.run(
            service._load_visual_inputs(
                source_media_url="https://example.test/source.png",
                source_media_type="image",
                user_text="生成图片",
            )
        )

    assert images == []
    assert vision_context == ""
    assert "MiniMax MCP vision failed; continuing without vision context." in caplog.text


def test_minimax_api_error_includes_trace_id() -> None:
    service = build_service()
    response = httpx.Response(
        200,
        json={"base_resp": {"status_code": 1004, "status_msg": "invalid api key"}},
        headers={"Trace-Id": "trace-123"},
        request=httpx.Request("POST", "https://api.minimaxi.com/v1/coding_plan/vlm"),
    )

    try:
        service._raise_for_minimax_api_error(
            response.json(),
            stage="MCP vision",
            response=response,
        )
    except ValueError as exc:
        assert "1004-invalid api key" in str(exc)
        assert "trace-123" in str(exc)
    else:
        raise AssertionError("Expected MiniMax API error to raise.")
