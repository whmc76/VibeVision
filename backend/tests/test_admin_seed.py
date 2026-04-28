import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.session import Base
from app.models import CreditLedgerEntry, LedgerReason, User
from app.routers.admin import list_users
from app.seed import (
    HIDDEN_ADMIN_GRANT_AMOUNT,
    HIDDEN_ADMIN_GRANT_NOTE,
    HIDDEN_ADMIN_USERNAME,
    seed_defaults,
)
from app.services.error_details import format_exception_details


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
