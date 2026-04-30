import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.session import Base
from app.models import CreditLedgerEntry, LedgerReason, TaskKind, User, Workflow
from app.routers.admin import list_users, list_workflows
from app.seed import (
    HIDDEN_ADMIN_GRANT_AMOUNT,
    HIDDEN_ADMIN_GRANT_NOTE,
    HIDDEN_ADMIN_USERNAME,
    seed_defaults,
)
from app.services.error_details import append_error_detail, format_exception_details


def build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_list_users_hides_hidden_accounts_by_default() -> None:
    with build_session() as db:
        db.add(User(username="visible-user", display_name="Visible User"))
        db.add(
            User(
                username="hidden-admin",
                display_name="Hidden Admin",
                is_admin=True,
                is_hidden=True,
            )
        )
        db.commit()

        visible_users = list_users(db=db, query=None, limit=50)
        visible_usernames = {user.username for user in visible_users}
        assert "visible-user" in visible_usernames
        assert "hidden-admin" not in visible_usernames

        matched_users = list_users(db=db, query="hidden-admin", limit=50)
        assert [user.username for user in matched_users] == ["hidden-admin"]


def test_seed_defaults_creates_hidden_admin_and_grants_credits_once() -> None:
    with build_session() as db:
        seed_defaults(db)
        user = db.scalar(select(User).where(User.username == HIDDEN_ADMIN_USERNAME))

        assert user is not None
        assert user.is_admin is True
        assert user.is_hidden is True
        assert user.credit_balance == HIDDEN_ADMIN_GRANT_AMOUNT

        seed_defaults(db)
        db.expire_all()
        user = db.scalar(select(User).where(User.username == HIDDEN_ADMIN_USERNAME))
        grants = list(
            db.scalars(
                select(CreditLedgerEntry).where(
                    CreditLedgerEntry.user_id == user.id,
                    CreditLedgerEntry.reason == LedgerReason.admin_adjustment,
                    CreditLedgerEntry.note == HIDDEN_ADMIN_GRANT_NOTE,
                )
            )
        )

        assert user.credit_balance == HIDDEN_ADMIN_GRANT_AMOUNT
        assert len(grants) == 1


def test_seed_defaults_retires_removed_workflows_and_admin_hides_them() -> None:
    with build_session() as db:
        db.add(
            Workflow(
                name="SDXL Prompt To Image",
                kind=TaskKind.image_generate,
                comfy_workflow_key="sdxl-text-to-image",
                credit_cost=6,
                is_active=True,
                template={"prompt": {"1": {"inputs": {}}}},
            )
        )
        db.add(
            Workflow(
                name="Text To Video",
                kind=TaskKind.video_text_to_video,
                comfy_workflow_key="text-to-video",
                credit_cost=10,
                is_active=True,
                template={"prompt": {"1": {"inputs": {}}}},
            )
        )
        db.commit()

        seed_defaults(db)

        retired = list(
            db.scalars(
                select(Workflow).where(
                    Workflow.comfy_workflow_key.in_(
                        {"sdxl-text-to-image", "text-to-video"}
                    )
                )
            )
        )
        visible_keys = {workflow.comfy_workflow_key for workflow in list_workflows(db=db)}

        assert retired
        assert all(workflow.is_active is False for workflow in retired)
        assert "sdxl-text-to-image" not in visible_keys
        assert "text-to-video" not in visible_keys
        assert visible_keys == {"flux2klein-single-edit", "z-image-turbo-text-to-image"}


def test_format_exception_details_includes_http_response_body() -> None:
    request = httpx.Request("POST", "http://127.0.0.1:8401/prompt")
    response = httpx.Response(
        400,
        request=request,
        json={"error": "missing prompt graph", "node": "sampler"},
    )

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = format_exception_details(exc)

    assert "Request: POST http://127.0.0.1:8401/prompt" in detail
    assert "Response status: 400 Bad Request" in detail
    assert '"error": "missing prompt graph"' in detail


def test_append_error_detail_includes_task_id() -> None:
    message = append_error_detail(
        "任务已完成，但结果回传到 Telegram 失败。管理员可在后台查看任务输出。",
        "Telegram API request timed out.",
        label="详细信息",
        task_id=42,
    )

    assert "详细信息: 任务 ID: 42; Telegram API request timed out." in message
