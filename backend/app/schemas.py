from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import MembershipTier, TaskKind, TaskStatus, UserStatus


class UserRead(BaseModel):
    id: int
    telegram_id: str | None
    username: str | None
    display_name: str | None
    status: UserStatus
    membership_tier: MembershipTier
    is_admin: bool
    is_hidden: bool
    credit_balance: int
    total_spent_credits: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    telegram_id: str | None = None
    username: str | None = None
    display_name: str | None = None
    membership_tier: MembershipTier = MembershipTier.free
    is_admin: bool = False
    is_hidden: bool = False
    credit_balance: int = 50


class UserUpdate(BaseModel):
    status: UserStatus | None = None
    membership_tier: MembershipTier | None = None
    display_name: str | None = None
    is_admin: bool | None = None
    is_hidden: bool | None = None


class CreditAdjustment(BaseModel):
    amount: int = Field(..., ge=-100000, le=100000)
    note: str | None = None


class WorkflowRead(BaseModel):
    id: int
    name: str
    kind: TaskKind
    comfy_workflow_key: str
    credit_cost: int
    is_active: bool
    description: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GenerationTaskRead(BaseModel):
    id: int
    user_id: int
    workflow_id: int | None
    kind: TaskKind
    status: TaskStatus
    original_text: str | None
    interpreted_prompt: str | None
    source_media_url: str | None
    result_urls: list[str]
    credit_cost: int
    error_message: str | None
    external_job_id: str | None
    telegram_chat_id: str | None
    telegram_message_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BotMessageRequest(BaseModel):
    telegram_id: str
    username: str | None = None
    display_name: str | None = None
    text: str | None = None
    source_media_url: str | None = None
    telegram_chat_id: str | None = None
    telegram_message_id: str | None = None


class BotMessageResponse(BaseModel):
    task_id: int
    status: TaskStatus
    kind: TaskKind
    credit_cost: int
    remaining_credits: int
    message: str


class DashboardStats(BaseModel):
    total_users: int
    active_users: int
    queued_tasks: int
    running_tasks: int
    completed_tasks: int
    credits_spent: int


class ServiceStatus(BaseModel):
    key: str
    name: str
    status: str
    url: str | None = None
    port: int | None = None
    pid: int | None = None
    process_name: str | None = None
    detail: str | None = None
    latency_ms: int | None = None
    can_start: bool = False
    can_stop: bool = False


class ServiceOverview(BaseModel):
    services: list[ServiceStatus]
    queue_running: int = 0
    queue_pending: int = 0


class ServiceActionResponse(BaseModel):
    service: str
    action: str
    ok: bool
    message: str
