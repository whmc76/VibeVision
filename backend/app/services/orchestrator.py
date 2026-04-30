import logging
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import GenerationTask, TaskKind, TaskStatus, User, Workflow
from app.schemas import BotMessageRequest
from app.services.comfyui import ComfyUIClient
from app.services.credits import (
    InsufficientCreditsError,
    available_credits,
    refresh_daily_bonus,
    refund_task_credits,
    reserve_for_task,
)
from app.services.error_details import format_exception_details
from app.services.intent import IntentService, TargetOutput
from app.services.users import get_or_create_telegram_user

logger = logging.getLogger(__name__)


class WorkflowUnavailableError(ValueError):
    pass


@dataclass(frozen=True)
class GenerationPreflight:
    target_output: TargetOutput
    source_media_type: str | None
    credit_cost: int
    available_credits: int


class GenerationOrchestrator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.intent = IntentService(settings)
        self.comfyui = ComfyUIClient(settings)

    async def handle_bot_message(self, db: Session, payload: BotMessageRequest) -> GenerationTask:
        task = await self.enqueue_bot_message(db, payload)
        return await self.execute_queued_task(db, task.id)

    async def enqueue_bot_message(self, db: Session, payload: BotMessageRequest) -> GenerationTask:
        request_started_at = time.perf_counter()
        user = self._get_or_create_user(db, payload)
        refresh_daily_bonus(db, user)
        workflows = self._list_active_workflows(db)
        target_output = self.intent.resolve_target_output(payload.text, payload.target_output)
        source_media_type = payload.source_media_type or ("image" if payload.source_media_url else None)
        candidate_workflows = self.intent.workflows_for_target(
            workflows,
            target_output=target_output,
            source_media_type=source_media_type,
            require_dispatchable=True,
        )
        if not candidate_workflows:
            raise WorkflowUnavailableError(
                self._unavailable_message(target_output, source_media_type)
            )

        self._ensure_can_afford_precheck(
            user,
            candidate_workflows,
            target_output=target_output,
            source_media_type=source_media_type,
        )
        logger.info(
            "generation_precheck_complete telegram_id=%s target=%s source_media=%s workflows=%s elapsed=%.2fs",
            payload.telegram_id,
            target_output.value,
            source_media_type or "none",
            len(candidate_workflows),
            time.perf_counter() - request_started_at,
        )

        stage_started_at = time.perf_counter()
        intent = await self.intent.classify(
            payload.text,
            available_workflows=candidate_workflows,
            has_image=bool(payload.source_media_url),
            source_media_url=payload.source_media_url,
            source_media_type=source_media_type,
            target_output=target_output,
            finalize_prompt=False,
        )
        logger.info(
            "generation_intent_complete telegram_id=%s workflow=%s kind=%s elapsed=%.2fs total=%.2fs",
            payload.telegram_id,
            intent.workflow_key,
            intent.kind.value,
            time.perf_counter() - stage_started_at,
            time.perf_counter() - request_started_at,
        )
        workflow = self._select_workflow(candidate_workflows, intent.workflow_key)
        self._ensure_can_afford_workflow(user, workflow)

        task = GenerationTask(
            user_id=user.id,
            workflow_id=workflow.id,
            kind=workflow.kind,
            status=TaskStatus.queued,
            original_text=payload.text,
            interpreted_prompt=intent.prompt,
            source_media_url=payload.source_media_url,
            credit_cost=workflow.credit_cost,
            telegram_chat_id=payload.telegram_chat_id,
            telegram_message_id=payload.telegram_message_id,
        )
        db.add(task)
        db.flush()

        bonus_used, paid_used = reserve_for_task(db, user, workflow.credit_cost, task_id=task.id)
        task.bonus_credit_cost = bonus_used
        task.paid_credit_cost = paid_used
        db.add(task)
        db.commit()
        db.refresh(task)
        logger.info(
            "generation_task_reserved task_id=%s workflow=%s cost=%s elapsed=%.2fs",
            task.id,
            workflow.comfy_workflow_key,
            workflow.credit_cost,
            time.perf_counter() - request_started_at,
        )
        return task

    def enqueue_regeneration(self, db: Session, source_task_id: int) -> GenerationTask:
        source_task = db.get(GenerationTask, source_task_id)
        if not source_task:
            raise ValueError(f"Task {source_task_id} not found.")
        if not source_task.user:
            raise ValueError(f"Task {source_task_id} has no user.")
        if not source_task.workflow:
            raise WorkflowUnavailableError(f"Task {source_task_id} has no workflow.")
        if not source_task.workflow.is_active:
            raise WorkflowUnavailableError(
                f"Workflow {source_task.workflow.comfy_workflow_key} is not active."
            )

        user = source_task.user
        workflow = source_task.workflow
        if self._has_active_regeneration(db, source_task):
            raise ValueError("同一任务已有重新生成在队列中，请等待当前任务完成后再试。")

        refresh_daily_bonus(db, user)
        self._ensure_can_afford_workflow(user, workflow)

        prompt = source_task.interpreted_prompt or source_task.original_text
        task = GenerationTask(
            user_id=user.id,
            workflow_id=workflow.id,
            kind=workflow.kind,
            status=TaskStatus.queued,
            original_text=source_task.original_text,
            interpreted_prompt=prompt,
            source_media_url=source_task.source_media_url,
            credit_cost=workflow.credit_cost,
            telegram_chat_id=source_task.telegram_chat_id,
            telegram_message_id=source_task.telegram_message_id,
        )
        db.add(task)
        db.flush()

        bonus_used, paid_used = reserve_for_task(db, user, workflow.credit_cost, task_id=task.id)
        task.bonus_credit_cost = bonus_used
        task.paid_credit_cost = paid_used
        db.add(task)
        db.commit()
        db.refresh(task)
        logger.info(
            "generation_task_regenerated source_task_id=%s task_id=%s workflow=%s cost=%s",
            source_task_id,
            task.id,
            workflow.comfy_workflow_key,
            workflow.credit_cost,
        )
        return task

    def _has_active_regeneration(self, db: Session, source_task: GenerationTask) -> bool:
        if not source_task.workflow_id:
            return False
        existing_id = db.scalar(
            select(GenerationTask.id)
            .where(
                GenerationTask.id != source_task.id,
                GenerationTask.user_id == source_task.user_id,
                GenerationTask.workflow_id == source_task.workflow_id,
                GenerationTask.original_text == source_task.original_text,
                GenerationTask.source_media_url == source_task.source_media_url,
                GenerationTask.telegram_chat_id == source_task.telegram_chat_id,
                GenerationTask.telegram_message_id == source_task.telegram_message_id,
                GenerationTask.status.in_((TaskStatus.queued, TaskStatus.running)),
            )
            .order_by(GenerationTask.id.desc())
            .limit(1)
        )
        return existing_id is not None

    async def execute_queued_task(self, db: Session, task_id: int) -> GenerationTask:
        request_started_at = time.perf_counter()
        task = db.get(GenerationTask, task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found.")
        if task.status != TaskStatus.queued:
            return task
        if not task.workflow:
            raise ValueError(f"Task {task_id} has no workflow.")
        workflow = task.workflow

        stage_started_at = time.perf_counter()
        try:
            enhanced_prompt = await self.intent.enhance_task_prompt(
                workflow=workflow,
                user_text=task.original_text,
                router_prompt=task.interpreted_prompt,
                source_media_url=task.source_media_url,
            )
            task.interpreted_prompt = enhanced_prompt
            logger.info(
                "generation_prompt_prepare_complete task_id=%s workflow=%s elapsed=%.2fs total=%.2fs",
                task.id,
                workflow.comfy_workflow_key,
                time.perf_counter() - stage_started_at,
                time.perf_counter() - request_started_at,
            )

            stage_started_at = time.perf_counter()
            task.external_job_id = await self.comfyui.submit_prompt(
                workflow=workflow,
                prompt=task.interpreted_prompt or task.original_text or "",
                source_media_url=task.source_media_url,
            )
            task.status = TaskStatus.running
        except Exception as exc:
            task.status = TaskStatus.failed
            task.error_message = format_exception_details(exc)
            if task.user:
                refund_task_credits(db, task.user, task)
            logger.exception(
                "generation_comfyui_submit_failed task_id=%s workflow=%s elapsed=%.2fs total=%.2fs",
                task.id,
                workflow.comfy_workflow_key,
                time.perf_counter() - stage_started_at,
                time.perf_counter() - request_started_at,
            )
        else:
            logger.info(
                "generation_comfyui_submit_complete task_id=%s workflow=%s prompt_id=%s elapsed=%.2fs total=%.2fs",
                task.id,
                workflow.comfy_workflow_key,
                task.external_job_id,
                time.perf_counter() - stage_started_at,
                time.perf_counter() - request_started_at,
            )

        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def preflight_bot_message(self, db: Session, payload: BotMessageRequest) -> GenerationPreflight:
        user = self._get_or_create_user(db, payload)
        refresh_daily_bonus(db, user)
        workflows = self._list_active_workflows(db)
        target_output = self.intent.resolve_target_output(payload.text, payload.target_output)
        source_media_type = payload.source_media_type or ("image" if payload.source_media_url else None)
        candidate_workflows = self.intent.workflows_for_target(
            workflows,
            target_output=target_output,
            source_media_type=source_media_type,
            require_dispatchable=True,
        )
        if not candidate_workflows:
            raise WorkflowUnavailableError(
                self._unavailable_message(target_output, source_media_type)
            )

        required_cost = self._precheck_credit_cost(
            candidate_workflows,
            target_output,
            source_media_type,
        )
        current_credits = available_credits(user)
        if current_credits < required_cost:
            raise InsufficientCreditsError(
                f"积分不足：{self._target_label(target_output)}任务预计需要 {required_cost} 积分，"
                f"当前可用 {current_credits} 积分。"
            )

        db.add(user)
        db.commit()
        return GenerationPreflight(
            target_output=target_output,
            source_media_type=source_media_type,
            credit_cost=required_cost,
            available_credits=current_credits,
        )

    def _get_or_create_user(self, db: Session, payload: BotMessageRequest) -> User:
        return get_or_create_telegram_user(
            db,
            telegram_id=payload.telegram_id,
            username=payload.username,
            display_name=payload.display_name,
        )

    def _list_active_workflows(self, db: Session) -> list[Workflow]:
        workflows = list(
            db.scalars(
                select(Workflow)
                .where(Workflow.is_active.is_(True))
                .order_by(Workflow.kind, Workflow.id)
            )
        )
        if not workflows:
            raise WorkflowUnavailableError("No active workflows are registered.")
        return workflows

    def _select_workflow(self, workflows: list[Workflow], workflow_key: str) -> Workflow:
        workflow = next(
            (item for item in workflows if item.comfy_workflow_key == workflow_key),
            None,
        )
        if not workflow:
            raise WorkflowUnavailableError(f"No active workflow registered for {workflow_key}.")
        return workflow

    def _ensure_can_afford_precheck(
        self,
        user: User,
        workflows: list[Workflow],
        *,
        target_output: TargetOutput,
        source_media_type: str | None,
    ) -> None:
        required_cost = self._precheck_credit_cost(workflows, target_output, source_media_type)
        if available_credits(user) >= required_cost:
            return
        raise InsufficientCreditsError(
            f"积分不足：{self._target_label(target_output)}任务预计需要 {required_cost} 积分，"
            f"当前可用 {available_credits(user)} 积分。"
        )

    def _precheck_credit_cost(
        self,
        workflows: list[Workflow],
        target_output: TargetOutput,
        source_media_type: str | None,
    ) -> int:
        if source_media_type and target_output == TargetOutput.image:
            edit_costs = [
                workflow.credit_cost for workflow in workflows if workflow.kind == TaskKind.image_edit
            ]
            if edit_costs:
                return min(edit_costs)
        if source_media_type and target_output == TargetOutput.video:
            image_to_video_costs = [
                workflow.credit_cost
                for workflow in workflows
                if workflow.kind == TaskKind.video_image_to_video
            ]
            if image_to_video_costs:
                return min(image_to_video_costs)
        return min(workflow.credit_cost for workflow in workflows)

    def _ensure_can_afford_workflow(self, user: User, workflow: Workflow) -> None:
        if available_credits(user) >= workflow.credit_cost:
            return
        raise InsufficientCreditsError(
            f"积分不足：本次任务需要 {workflow.credit_cost} 积分，"
            f"当前可用 {available_credits(user)} 积分。"
        )

    def _unavailable_message(
        self,
        target_output: TargetOutput,
        source_media_type: str | None,
    ) -> str:
        if target_output == TargetOutput.video and not source_media_type:
            return "当前没有可用的文生视频工作流；如果要做图生视频，请选择视频并上传图片。"
        if target_output == TargetOutput.video:
            return "当前没有可用的视频生成工作流，请先在后台配置有效的 ComfyUI 视频模板。"
        if source_media_type:
            return "当前没有可用的图片编辑工作流，请先在后台配置有效的 ComfyUI 图片模板。"
        return "当前没有可用的文生图工作流，请先在后台配置有效的 ComfyUI 图片模板。"

    def _target_label(self, target_output: TargetOutput) -> str:
        return "视频" if target_output == TargetOutput.video else "图片"
