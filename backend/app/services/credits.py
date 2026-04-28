from sqlalchemy.orm import Session

from app.models import CreditLedgerEntry, LedgerReason, User


class InsufficientCreditsError(ValueError):
    pass


def adjust_credits(
    db: Session,
    user: User,
    amount: int,
    reason: LedgerReason,
    note: str | None = None,
    task_id: int | None = None,
) -> CreditLedgerEntry:
    if user.credit_balance + amount < 0:
        raise InsufficientCreditsError("User does not have enough credits.")

    user.credit_balance += amount
    if amount < 0:
        user.total_spent_credits += abs(amount)

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


def reserve_for_task(db: Session, user: User, amount: int, task_id: int | None = None) -> None:
    adjust_credits(
        db=db,
        user=user,
        amount=-amount,
        reason=LedgerReason.task_reserved,
        note="Reserved credits for generation task.",
        task_id=task_id,
    )
