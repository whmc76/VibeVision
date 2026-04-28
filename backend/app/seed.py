import json
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    GenerationTask,
    MembershipTier,
    TaskKind,
    TaskStatus,
    User,
    UserStatus,
    Workflow,
)

TEMPLATE_DIR = Path(__file__).resolve().parent / "workflow_templates"


def load_template(filename: str) -> dict:
    return json.loads((TEMPLATE_DIR / filename).read_text(encoding="utf-8"))


DEFAULT_WORKFLOWS = [
    {
        "name": "SDXL Prompt To Image",
        "kind": TaskKind.image_generate,
        "comfy_workflow_key": "sdxl-text-to-image",
        "credit_cost": 6,
        "description": "Generate images from text prompts.",
        "template": {},
    },
    {
        "name": "Image Edit Assistant",
        "kind": TaskKind.image_edit,
        "comfy_workflow_key": "flux2klein-single-edit",
        "credit_cost": 8,
        "description": "Edit uploaded images with the Flux2 Klein single-edit workflow.",
        "template": load_template("flux2klein_single_edit_api.json"),
    },
    {
        "name": "Image To Video Motion",
        "kind": TaskKind.video_image_to_video,
        "comfy_workflow_key": "image-to-video",
        "credit_cost": 18,
        "description": "Animate user-provided images into short videos.",
        "template": {},
    },
    {
        "name": "Image Understanding Prompt Writer",
        "kind": TaskKind.prompt_expand,
        "comfy_workflow_key": "prompt-expand",
        "credit_cost": 2,
        "description": "Understand media and expand user intent into generation prompts.",
        "template": {},
    },
]


def seed_defaults(db: Session, include_demo: bool = False) -> None:
    for item in DEFAULT_WORKFLOWS:
        exists = db.scalar(
            select(Workflow).where(
                or_(
                    Workflow.comfy_workflow_key == item["comfy_workflow_key"],
                    Workflow.name == item["name"],
                )
            )
        )
        if not exists:
            db.add(Workflow(**item))
            continue

        exists.name = item["name"]
        exists.kind = item["kind"]
        exists.comfy_workflow_key = item["comfy_workflow_key"]
        exists.credit_cost = item["credit_cost"]
        exists.description = item["description"]
        exists.template = item["template"]
        exists.is_active = True
        db.add(exists)

    db.flush()
    legacy_edit_workflow = db.scalar(
        select(Workflow).where(Workflow.comfy_workflow_key == "image-edit-inpaint")
    )
    if legacy_edit_workflow:
        legacy_edit_workflow.is_active = False
        db.add(legacy_edit_workflow)
    db.flush()

    if include_demo:
        seed_demo_data(db)

    db.commit()


def seed_demo_data(db: Session) -> None:
    has_users = db.scalar(select(User.id).limit(1))
    if has_users:
        return

    users = [
        User(
            telegram_id="711820445",
            username="studio_mira",
            display_name="Mira Chen",
            status=UserStatus.active,
            membership_tier=MembershipTier.studio,
            credit_balance=1840,
            total_spent_credits=20390,
        ),
        User(
            telegram_id="503188902",
            username="framecraft",
            display_name="Frame Craft",
            status=UserStatus.active,
            membership_tier=MembershipTier.pro,
            credit_balance=612,
            total_spent_credits=9340,
        ),
        User(
            telegram_id="913588201",
            username="nora_ai",
            display_name="Nora",
            status=UserStatus.limited,
            membership_tier=MembershipTier.starter,
            credit_balance=76,
            total_spent_credits=1288,
        ),
    ]
    db.add_all(users)
    db.flush()

    workflows = {
        workflow.comfy_workflow_key: workflow
        for workflow in db.scalars(select(Workflow)).all()
    }

    db.add_all(
        [
            GenerationTask(
                user_id=users[0].id,
                workflow_id=workflows["sdxl-text-to-image"].id,
                kind=TaskKind.image_generate,
                status=TaskStatus.running,
                original_text="赛博茶室，夜景，柔和灯光",
                interpreted_prompt=(
                    "A cinematic cyberpunk tea room at night with soft practical lighting."
                ),
                credit_cost=6,
                external_job_id="demo-running-88",
            ),
            GenerationTask(
                user_id=users[1].id,
                workflow_id=workflows["image-to-video"].id,
                kind=TaskKind.video_image_to_video,
                status=TaskStatus.queued,
                original_text="让这张产品图镜头慢慢推进",
                interpreted_prompt="Slow camera push-in with subtle product light movement.",
                source_media_url="https://example.com/source.png",
                credit_cost=18,
            ),
            GenerationTask(
                user_id=users[2].id,
                workflow_id=workflows["flux2klein-single-edit"].id,
                kind=TaskKind.image_edit,
                status=TaskStatus.failed,
                original_text="把背景改成高级灰展厅",
                interpreted_prompt="Replace the background with a refined neutral gallery showroom.",
                source_media_url="https://example.com/edit.png",
                credit_cost=8,
                error_message="ComfyUI timeout after 30s",
                external_job_id="demo-failed-71",
            ),
        ]
    )
