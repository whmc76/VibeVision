import asyncio

import httpx

from app.core.config import Settings
from app.models import TaskKind, Workflow
from app.services.intent import IntentResult, IntentService


def build_workflows() -> list[Workflow]:
    return [
        Workflow(
            name="SDXL Prompt To Image",
            kind=TaskKind.image_generate,
            comfy_workflow_key="sdxl-text-to-image",
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

    assert result.workflow_key == "sdxl-text-to-image"
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

    for text in ["改图，把背景换成海边", "改照片，保留人物换背景"]:
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


def test_fallback_selects_video_workflow_for_motion_request() -> None:
    service = build_service()
    routes = service._build_workflow_routes(build_workflows())

    result = service._fallback_classify(
        text="animate this image with a slow push-in",
        workflow_routes=routes,
        media_attached=True,
    )

    assert result.workflow_key == "image-to-video"
    assert result.kind == TaskKind.video_image_to_video


def test_fallback_target_video_with_image_prefers_image_to_video() -> None:
    service = build_service()
    routes = service._build_workflow_routes(build_workflows())

    result = service._fallback_classify(
        text="生成视频，让镜头慢慢推进",
        workflow_routes=routes,
        media_attached=True,
        source_media_type="image",
        target_output=service.resolve_target_output("生成视频"),
    )

    assert result.workflow_key == "image-to-video"
    assert result.kind == TaskKind.video_image_to_video


def test_fallback_selects_prompt_expand_for_prompt_help() -> None:
    service = build_service()
    routes = service._build_workflow_routes(build_workflows())

    result = service._fallback_classify(
        text="帮我扩写提示词",
        workflow_routes=routes,
        media_attached=False,
    )

    assert result.workflow_key == "prompt-expand"
    assert result.kind == TaskKind.prompt_expand


def test_classify_routes_empty_media_request_to_prompt_expand(monkeypatch) -> None:
    service = build_service()

    async def fake_load_images(source_media_url: str) -> list[str]:
        return []

    monkeypatch.setattr(service, "_load_ollama_images", fake_load_images)

    result = asyncio.run(
        service.classify(
            text=None,
            available_workflows=build_workflows(),
            has_image=True,
            source_media_url="https://example.com/source.png",
        )
    )

    assert result.workflow_key == "prompt-expand"
    assert result.kind == TaskKind.prompt_expand


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

    assert result.workflow_key == "sdxl-text-to-image"
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
        '<think>pick a workflow</think>{"workflow_key":"sdxl-text-to-image","prompt":"cat"}'
    )

    assert data == {"workflow_key": "sdxl-text-to-image", "prompt": "cat"}


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
            workflow_key="sdxl-text-to-image",
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
