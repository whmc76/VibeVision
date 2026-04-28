from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models import (
    GenerationTask,
    LedgerReason,
    TaskStatus,
    User,
    UserStatus,
    Workflow,
)
from app.schemas import (
    CreditAdjustment,
    DashboardStats,
    GenerationTaskRead,
    ServiceActionResponse,
    ServiceOverview,
    UserCreate,
    UserRead,
    UserUpdate,
    WorkflowRead,
)
from app.services.credits import InsufficientCreditsError, adjust_credits
from app.services.service_monitor import ServiceMonitor

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stats", response_model=DashboardStats)
def dashboard_stats(db: Session = Depends(get_db)) -> DashboardStats:
    total_users = db.scalar(select(func.count()).select_from(User)) or 0
    active_users = (
        db.scalar(select(func.count()).select_from(User).where(User.status == UserStatus.active)) or 0
    )
    queued_tasks = (
        db.scalar(
            select(func.count()).select_from(GenerationTask).where(GenerationTask.status == TaskStatus.queued)
        )
        or 0
    )
    running_tasks = (
        db.scalar(
            select(func.count()).select_from(GenerationTask).where(GenerationTask.status == TaskStatus.running)
        )
        or 0
    )
    completed_tasks = (
        db.scalar(
            select(func.count())
            .select_from(GenerationTask)
            .where(GenerationTask.status == TaskStatus.completed)
        )
        or 0
    )
    credits_spent = db.scalar(select(func.coalesce(func.sum(User.total_spent_credits), 0))) or 0
    return DashboardStats(
        total_users=total_users,
        active_users=active_users,
        queued_tasks=queued_tasks,
        running_tasks=running_tasks,
        completed_tasks=completed_tasks,
        credits_spent=credits_spent,
    )


@router.get("/users", response_model=list[UserRead])
def list_users(
    db: Session = Depends(get_db),
    query: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[User]:
    statement = select(User).order_by(User.created_at.desc()).limit(limit)
    if query:
        like = f"%{query}%"
        statement = (
            select(User)
            .where(User.username.ilike(like) | User.display_name.ilike(like) | User.telegram_id.ilike(like))
            .order_by(User.created_at.desc())
            .limit(limit)
        )
    return list(db.scalars(statement))


@router.post("/users", response_model=UserRead, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    user = User(**payload.model_dump())
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db)) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/credits", response_model=UserRead)
def adjust_user_credits(
    user_id: int,
    payload: CreditAdjustment,
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    try:
        adjust_credits(
            db,
            user,
            payload.amount,
            LedgerReason.admin_adjustment,
            note=payload.note,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(user)
    return user


@router.get("/tasks", response_model=list[GenerationTaskRead])
def list_tasks(
    db: Session = Depends(get_db),
    status: TaskStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[GenerationTask]:
    statement = select(GenerationTask).order_by(GenerationTask.created_at.desc()).limit(limit)
    if status:
        statement = (
            select(GenerationTask)
            .where(GenerationTask.status == status)
            .order_by(GenerationTask.created_at.desc())
            .limit(limit)
        )
    return list(db.scalars(statement))


@router.get("/workflows", response_model=list[WorkflowRead])
def list_workflows(db: Session = Depends(get_db)) -> list[Workflow]:
    return list(db.scalars(select(Workflow).order_by(Workflow.kind, Workflow.name)))


@router.get("/services", response_model=ServiceOverview)
async def service_overview() -> ServiceOverview:
    return await ServiceMonitor(get_settings()).overview()


@router.post("/services/{service}/start", response_model=ServiceActionResponse)
async def start_service(service: str) -> ServiceActionResponse:
    return await ServiceMonitor(get_settings()).start(service)


@router.post("/services/{service}/stop", response_model=ServiceActionResponse)
async def stop_service(service: str) -> ServiceActionResponse:
    return await ServiceMonitor(get_settings()).stop(service)


@router.post("/services/{service}/restart", response_model=ServiceActionResponse)
async def restart_service(service: str) -> ServiceActionResponse:
    return await ServiceMonitor(get_settings()).restart(service)
