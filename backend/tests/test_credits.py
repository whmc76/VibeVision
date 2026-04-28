from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.session import Base
from app.models import MembershipTier, User
from app.services.credits import apply_recharge, refresh_daily_bonus, reserve_for_task


def build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_first_recharge_promotes_member_and_applies_daily_bonus() -> None:
    with build_session() as db:
        user = User(credit_balance=5)
        db.add(user)
        db.flush()

        apply_recharge(db, user, "monthly")

        assert user.membership_tier == MembershipTier.starter
        assert user.credit_balance == 135
        assert user.daily_bonus_allowance == 10
        assert user.daily_bonus_balance == 10
        assert user.total_recharge_usd_cents == 990


def test_daily_bonus_resets_without_accumulating() -> None:
    with build_session() as db:
        user = User(
            credit_balance=0,
            daily_bonus_allowance=10,
            daily_bonus_balance=3,
            daily_bonus_granted_on=date.today() - timedelta(days=1),
        )
        db.add(user)
        db.flush()

        refresh_daily_bonus(db, user)

        assert user.daily_bonus_balance == 0


def test_task_reservation_consumes_daily_bonus_before_paid_credits() -> None:
    with build_session() as db:
        user = User(credit_balance=20, daily_bonus_balance=7, daily_bonus_granted_on=date.today())
        db.add(user)
        db.flush()

        bonus_used, paid_used = reserve_for_task(db, user, 10)

        assert bonus_used == 7
        assert paid_used == 3
        assert user.daily_bonus_balance == 0
        assert user.credit_balance == 17


def test_vip_and_svip_recharge_multipliers_follow_cumulative_thresholds() -> None:
    with build_session() as db:
        vip_user = User(credit_balance=0, total_recharge_usd_cents=9_010)
        svip_user = User(credit_balance=0, total_recharge_usd_cents=49_010)
        db.add_all([vip_user, svip_user])
        db.flush()

        apply_recharge(db, vip_user, "monthly")
        apply_recharge(db, svip_user, "monthly")

        assert vip_user.membership_tier == MembershipTier.pro
        assert vip_user.credit_balance == 110
        assert svip_user.membership_tier == MembershipTier.studio
        assert svip_user.credit_balance == 120


def test_premium_subscription_grants_330_base_credits() -> None:
    with build_session() as db:
        user = User(credit_balance=0)
        db.add(user)
        db.flush()

        apply_recharge(db, user, "premium")

        assert user.credit_balance == 360
        assert user.daily_bonus_allowance == 30
        assert user.total_recharge_usd_cents == 2_990
