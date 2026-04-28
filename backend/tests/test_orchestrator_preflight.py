import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import Base
from app.models import TaskKind, User, Workflow
from app.schemas import BotMessageRequest
from app.services.credits import InsufficientCreditsError
from app.services.intent import TargetOutput
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
