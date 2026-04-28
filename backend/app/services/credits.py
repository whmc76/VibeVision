from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.models import CreditLedgerEntry, GenerationTask, LedgerReason, MembershipTier, User


class InsufficientCreditsError(ValueError):
    pass


class UnknownRechargePlanError(ValueError):
    pass


@dataclass(frozen=True)
class RechargePlan:
    key: str
    label: str
    price_usd_cents: int
    base_credits: int
    daily_bonus_allowance: int


RECHARGE_PLANS = {
    "monthly": RechargePlan(
        key="monthly",
        label="月度订阅",
        price_usd_cents=990,
        base_credits=100,
        daily_bonus_allowance=10,
    ),
    "premium": RechargePlan(
        key="premium",
        label="高级订阅",
        price_usd_cents=2990,
        base_credits=330,
        daily_bonus_allowance=30,
    ),
}
FIRST_RECHARGE_BONUS_CREDITS = 30
VIP_THRESHOLD_USD_CENTS = 10_000
SVIP_THRESHOLD_USD_CENTS = 50_000
SUBSCRIPTION_DAYS = 30


def adjust_credits(
    db: Session,
    user: User,
    amount: int,
    reason: LedgerReason,
    note: str | None = None,
    task_id: int | None = None,
) -> CreditLedgerEntry:
    credit_balance = _int_value(user.credit_balance)
    if credit_balance + amount < 0:
        raise InsufficientCreditsError("User does not have enough credits.")

    user.credit_balance = credit_balance + amount
    if amount < 0:
        user.total_spent_credits = _int_value(user.total_spent_credits) + abs(amount)

    entry = CreditLedgerEntry(
        user_id=user.id,
        amount=amount,
        reason=reason,
        note=note,
        task_id=task_id,
    )
    db.add(entry)
    db.add(user)
    return entry


def available_credits(user: User) -> int:
    return _int_value(user.credit_balance) + _int_value(user.daily_bonus_balance)


def refresh_daily_bonus(db: Session, user: User, today: date | None = None) -> None:
    today = today or date.today()
    if user.daily_bonus_granted_on == today:
        return

    allowance = _active_daily_allowance(user)
    user.daily_bonus_allowance = allowance
    user.daily_bonus_balance = allowance
    user.daily_bonus_granted_on = today
    db.add(user)
    if allowance > 0:
        db.add(
            CreditLedgerEntry(
                user_id=user.id,
                amount=allowance,
                reason=LedgerReason.daily_bonus_reset,
                note="Daily bonus reset.",
            )
        )


def apply_recharge(db: Session, user: User, plan_key: str) -> RechargePlan:
    try:
        plan = RECHARGE_PLANS[plan_key]
    except KeyError as exc:
        raise UnknownRechargePlanError(f"Unknown recharge plan: {plan_key}.") from exc

    was_first_recharge = _int_value(user.total_recharge_usd_cents) == 0
    user.total_recharge_usd_cents = _int_value(user.total_recharge_usd_cents) + plan.price_usd_cents
    user.membership_tier = _tier_for_total_recharge(user.total_recharge_usd_cents)
    user.subscription_plan = plan.key
    user.subscription_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(
        days=SUBSCRIPTION_DAYS
    )
    user.daily_bonus_allowance = plan.daily_bonus_allowance
    user.daily_bonus_granted_on = None

    purchased_credits = _apply_tier_multiplier(plan.base_credits, user.membership_tier)
    first_recharge_bonus = FIRST_RECHARGE_BONUS_CREDITS if was_first_recharge else 0
    granted_credits = purchased_credits + first_recharge_bonus
    user.credit_balance = _int_value(user.credit_balance) + granted_credits
    db.add(user)
    db.add(
        CreditLedgerEntry(
            user_id=user.id,
            amount=granted_credits,
            reason=LedgerReason.recharge_purchase,
            note=(
                f"{plan.label}: {purchased_credits} credits"
                f"{' + first recharge bonus 30' if first_recharge_bonus else ''}."
            ),
        )
    )
    refresh_daily_bonus(db, user)
    return plan


def reserve_for_task(
    db: Session,
    user: User,
    amount: int,
    task_id: int | None = None,
) -> tuple[int, int]:
    refresh_daily_bonus(db, user)
    if available_credits(user) < amount:
        raise InsufficientCreditsError("User does not have enough credits.")

    bonus_used = min(_int_value(user.daily_bonus_balance), amount)
    paid_used = amount - bonus_used
    user.daily_bonus_balance = _int_value(user.daily_bonus_balance) - bonus_used
    user.credit_balance = _int_value(user.credit_balance) - paid_used
    user.total_spent_credits = _int_value(user.total_spent_credits) + amount

    if bonus_used:
        db.add(
            CreditLedgerEntry(
                user_id=user.id,
                amount=-bonus_used,
                reason=LedgerReason.task_reserved,
                note="Reserved daily bonus credits for generation task.",
                task_id=task_id,
            )
        )
    if paid_used:
        db.add(
            CreditLedgerEntry(
                user_id=user.id,
                amount=-paid_used,
                reason=LedgerReason.task_reserved,
                note="Reserved paid credits for generation task.",
                task_id=task_id,
            )
        )
    db.add(user)
    return bonus_used, paid_used


def refund_task_credits(db: Session, user: User, task: GenerationTask) -> None:
    bonus_amount = _int_value(task.bonus_credit_cost)
    paid_amount = _int_value(task.paid_credit_cost)
    if not bonus_amount and not paid_amount:
        paid_amount = _int_value(task.credit_cost)

    if bonus_amount:
        user.daily_bonus_balance = _int_value(user.daily_bonus_balance) + bonus_amount
        db.add(
            CreditLedgerEntry(
                user_id=user.id,
                amount=bonus_amount,
                reason=LedgerReason.task_refunded,
                note="Refunded daily bonus credits after generation failure.",
                task_id=task.id,
            )
        )
    if paid_amount:
        user.credit_balance = _int_value(user.credit_balance) + paid_amount
        db.add(
            CreditLedgerEntry(
                user_id=user.id,
                amount=paid_amount,
                reason=LedgerReason.task_refunded,
                note="Refunded paid credits after generation failure.",
                task_id=task.id,
            )
        )
    db.add(user)


def _active_daily_allowance(user: User) -> int:
    expires_at = user.subscription_expires_at
    if not expires_at:
        return 0
    if expires_at < datetime.now(UTC).replace(tzinfo=None):
        return 0
    return _int_value(user.daily_bonus_allowance)


def _tier_for_total_recharge(total_usd_cents: int) -> MembershipTier:
    if total_usd_cents >= SVIP_THRESHOLD_USD_CENTS:
        return MembershipTier.studio
    if total_usd_cents >= VIP_THRESHOLD_USD_CENTS:
        return MembershipTier.pro
    if total_usd_cents > 0:
        return MembershipTier.starter
    return MembershipTier.free


def _apply_tier_multiplier(credits: int, tier: MembershipTier) -> int:
    multiplier = Decimal("1")
    if tier == MembershipTier.pro:
        multiplier = Decimal("1.1")
    elif tier == MembershipTier.studio:
        multiplier = Decimal("1.2")
    return int((Decimal(credits) * multiplier).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _int_value(value: int | None) -> int:
    return int(value or 0)
