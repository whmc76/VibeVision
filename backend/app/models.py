from datetime import date, datetime
from enum import StrEnum
from secrets import token_hex

from sqlalchemy import JSON, Date, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def generate_task_public_id() -> str:
    return token_hex(6)


class UserStatus(StrEnum):
    active = "active"
    limited = "limited"
    banned = "banned"


class MembershipTier(StrEnum):
    # Stored values are kept stable for existing local databases.
    free = "free"
    starter = "starter"
    pro = "pro"
    studio = "studio"


class TaskKind(StrEnum):
    image_generate = "image.generate"
    image_edit = "image.edit"
    video_text_to_video = "video.text_to_video"
    video_image_to_video = "video.image_to_video"
    prompt_expand = "prompt.expand"


class TaskStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class LedgerReason(StrEnum):
    signup_grant = "signup_grant"
    admin_adjustment = "admin_adjustment"
    recharge_purchase = "recharge_purchase"
    daily_bonus_reset = "daily_bonus_reset"
    task_reserved = "task_reserved"
    task_refunded = "task_refunded"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128), index=True)
    display_name: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.active)
    membership_tier: Mapped[MembershipTier] = mapped_column(
        Enum(MembershipTier), default=MembershipTier.free
    )
    is_admin: Mapped[bool] = mapped_column(default=False)
    is_hidden: Mapped[bool] = mapped_column(default=False)
    credit_balance: Mapped[int] = mapped_column(Integer, default=5)
    daily_bonus_balance: Mapped[int] = mapped_column(Integer, default=0)
    daily_bonus_allowance: Mapped[int] = mapped_column(Integer, default=0)
    daily_bonus_granted_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    subscription_plan: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subscription_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_recharge_usd_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_spent_credits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    tasks: Mapped[list["GenerationTask"]] = relationship(back_populates="user")
    ledger_entries: Mapped[list["CreditLedgerEntry"]] = relationship(back_populates="user")


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    kind: Mapped[TaskKind] = mapped_column(Enum(TaskKind), index=True)
    comfy_workflow_key: Mapped[str] = mapped_column(String(160), unique=True)
    credit_cost: Mapped[int] = mapped_column(Integer, default=5)
    is_active: Mapped[bool] = mapped_column(default=True)
    description: Mapped[str | None] = mapped_column(Text)
    template: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    tasks: Mapped[list["GenerationTask"]] = relationship(back_populates="workflow")


class GenerationTask(Base):
    __tablename__ = "generation_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(24),
        default=generate_task_public_id,
        unique=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id"), nullable=True)
    kind: Mapped[TaskKind] = mapped_column(Enum(TaskKind), index=True)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.queued)
    original_text: Mapped[str | None] = mapped_column(Text)
    interpreted_prompt: Mapped[str | None] = mapped_column(Text)
    source_media_url: Mapped[str | None] = mapped_column(Text)
    result_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    credit_cost: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    external_job_id: Mapped[str | None] = mapped_column(String(160), index=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), index=True)
    telegram_message_id: Mapped[str | None] = mapped_column(String(64), index=True)
    bonus_credit_cost: Mapped[int] = mapped_column(Integer, default=0)
    paid_credit_cost: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="tasks")
    workflow: Mapped[Workflow | None] = relationship(back_populates="tasks")


class CreditLedgerEntry(Base):
    __tablename__ = "credit_ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[LedgerReason] = mapped_column(Enum(LedgerReason), index=True)
    note: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("generation_tasks.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="ledger_entries")
