import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import Base
from app.models import GenerationTask, TaskKind, TaskStatus, User, Workflow
from app.schemas import BotMessageRequest
from app.services.credits import InsufficientCreditsError
from app.services.intent import IntentResult, TargetOutput
from app.services.orchestrator import GenerationOrchestrator


def build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def dispatchable_workflow(kind: TaskKind, cost: int) -> Workflow:
    return Workflow(
        name=f"{kind.value}-{cost}",
        kind=kind,
        comfy_workflow_key=f"{kind.value}-{cost}",
        credit_cost=cost,
        is_active=True,
        template={"prompt": {"1": {"inputs": {}}}},
    )


def test_preflight_rejects_insufficient_credits_without_llm_or_task() -> None:
    with build_session() as db:
        db.add(User(telegram_id="1", credit_balance=0))
        db.add(dispatchable_workflow(TaskKind.video_text_to_video, 10))
        db.commit()

        payload = BotMessageRequest(
            telegram_id="1",
            text="生成视频 一只猫在跑步",
            target_output=TargetOutput.video.value,
        )

        with pytest.raises(InsufficientCreditsError, match="预计需要 10 积分"):
            GenerationOrchestrator(Settings()).preflight_bot_message(db, payload)


def test_preflight_returns_target_cost_before_generation() -> None:
    with build_session() as db:
        db.add(User(telegram_id="1", credit_balance=5))
        db.add(dispatchable_workflow(TaskKind.image_generate, 1))
        db.commit()

        payload = BotMessageRequest(
            telegram_id="1",
            text="生图 一张产品海报",
            target_output=TargetOutput.image.value,
        )

        preflight = GenerationOrchestrator(Settings()).preflight_bot_message(db, payload)

        assert preflight.target_output == TargetOutput.image
        assert preflight.credit_cost == 1
        assert preflight.available_credits == 5


def test_enqueue_creates_queued_task_without_comfyui_submission() -> None:
    with build_session() as db:
        db.add(User(telegram_id="1", credit_balance=5))
        db.add(dispatchable_workflow(TaskKind.image_generate, 1))
        db.commit()

        orchestrator = GenerationOrchestrator(Settings())

        async def fake_classify(*args, **kwargs):
            assert kwargs["finalize_prompt"] is False
            return IntentResult(
                workflow_key="image.generate-1",
                kind=TaskKind.image_generate,
                prompt="router prompt",
                confidence=0.9,
            )

        async def fail_submit(**kwargs):
            raise AssertionError("enqueue should not submit to ComfyUI")

        orchestrator.intent.classify = fake_classify
        orchestrator.comfyui.submit_prompt = fail_submit

        task = asyncio.run(
            orchestrator.enqueue_bot_message(
                db,
                BotMessageRequest(
                    telegram_id="1",
                    text="生图 一张产品海报",
                    target_output=TargetOutput.image.value,
                ),
            )
        )

        assert task.status == TaskStatus.queued
        assert task.external_job_id is None
        assert task.interpreted_prompt == "router prompt"
        assert task.credit_cost == 1


def test_execute_queued_task_enhances_prompt_and_submits_to_comfyui() -> None:
    with build_session() as db:
        db.add(User(telegram_id="1", credit_balance=5))
        db.add(dispatchable_workflow(TaskKind.image_generate, 1))
        db.commit()

        orchestrator = GenerationOrchestrator(Settings())

        async def fake_classify(*args, **kwargs):
            return IntentResult(
                workflow_key="image.generate-1",
                kind=TaskKind.image_generate,
                prompt="router prompt",
                confidence=0.9,
            )

        async def fake_enhance(**kwargs):
            assert kwargs["router_prompt"] == "router prompt"
            return "enhanced prompt"

        async def fake_submit(**kwargs):
            assert kwargs["prompt"] == "enhanced prompt"
            return "prompt-123"

        orchestrator.intent.classify = fake_classify
        orchestrator.intent.enhance_task_prompt = fake_enhance
        orchestrator.comfyui.submit_prompt = fake_submit

        task = asyncio.run(
            orchestrator.enqueue_bot_message(
                db,
                BotMessageRequest(
                    telegram_id="1",
                    text="生图 一张产品海报",
                    target_output=TargetOutput.image.value,
                ),
            )
        )

        task = asyncio.run(orchestrator.execute_queued_task(db, task.id))

        assert task.status == TaskStatus.running
        assert task.external_job_id == "prompt-123"
        assert task.interpreted_prompt == "enhanced prompt"


def test_enqueue_regeneration_clones_source_task_and_reserves_credits() -> None:
    with build_session() as db:
        user = User(telegram_id="1", credit_balance=5)
        workflow = dispatchable_workflow(TaskKind.image_generate, 2)
        db.add(user)
        db.add(workflow)
        db.commit()

        source_task = GenerationTask(
            user_id=user.id,
            workflow_id=workflow.id,
            kind=workflow.kind,
            status=TaskStatus.completed,
            original_text="生图 一张产品海报",
            interpreted_prompt="enhanced prompt",
            source_media_url="https://example.test/source.png",
            result_urls=["https://example.test/result.png"],
            credit_cost=workflow.credit_cost,
            telegram_chat_id="456",
            telegram_message_id="8",
        )
        db.add(source_task)
        db.commit()

        task = GenerationOrchestrator(Settings()).enqueue_regeneration(db, source_task.id)

        assert task.id != source_task.id
        assert task.status == TaskStatus.queued
        assert task.workflow_id == workflow.id
        assert task.original_text == source_task.original_text
        assert task.interpreted_prompt == source_task.interpreted_prompt
        assert task.source_media_url == source_task.source_media_url
        assert task.result_urls == []
        assert task.external_job_id is None
        assert task.credit_cost == 2
        assert task.paid_credit_cost == 2
        assert user.credit_balance == 3


def test_enqueue_regeneration_rejects_existing_active_clone() -> None:
    with build_session() as db:
        user = User(telegram_id="1", credit_balance=5)
        workflow = dispatchable_workflow(TaskKind.image_generate, 2)
        db.add(user)
        db.add(workflow)
        db.commit()

        source_task = GenerationTask(
            user_id=user.id,
            workflow_id=workflow.id,
            kind=workflow.kind,
            status=TaskStatus.completed,
            original_text="生图 一张产品海报",
            interpreted_prompt="enhanced prompt",
            source_media_url="https://example.test/source.png",
            result_urls=["https://example.test/result.png"],
            credit_cost=workflow.credit_cost,
            telegram_chat_id="456",
            telegram_message_id="8",
        )
        active_clone = GenerationTask(
            user_id=user.id,
            workflow_id=workflow.id,
            kind=workflow.kind,
            status=TaskStatus.queued,
            original_text=source_task.original_text,
            interpreted_prompt=source_task.interpreted_prompt,
            source_media_url=source_task.source_media_url,
            result_urls=[],
            credit_cost=workflow.credit_cost,
            telegram_chat_id=source_task.telegram_chat_id,
            telegram_message_id=source_task.telegram_message_id,
        )
        db.add_all([source_task, active_clone])
        db.commit()

        try:
            GenerationOrchestrator(Settings()).enqueue_regeneration(db, source_task.id)
        except ValueError as exc:
            assert "已有重新生成" in str(exc)
        else:
            raise AssertionError("Expected active regeneration clone to be rejected.")
