import json
from pathlib import Path

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.models import (
    CreditLedgerEntry,
    GenerationTask,
    LedgerReason,
    TaskKind,
    User,
    UserStatus,
    Workflow,
)
from app.services.credits import adjust_credits
from app.workflows import RETIRED_WORKFLOW_KEYS

TEMPLATE_DIR = Path(__file__).resolve().parent / "workflow_templates"
DEMO_TELEGRAM_IDS = ("711820445", "503188902", "913588201")
HIDDEN_ADMIN_USERNAME = "cyberdicklang"
HIDDEN_ADMIN_DISPLAY_NAME = "CyberDickLang"
HIDDEN_ADMIN_GRANT_AMOUNT = 1_000_000
HIDDEN_ADMIN_GRANT_NOTE = "Bootstrap hidden admin credit grant."


def load_template(filename: str) -> dict:
    return json.loads((TEMPLATE_DIR / filename).read_text(encoding="utf-8"))


def _is_dispatchable_template(template: dict) -> bool:
    if not template:
        return False
    if isinstance(template.get("prompt"), dict):
        return True
    return all(
        isinstance(node, dict) and isinstance(node.get("class_type"), str)
        for node in template.values()
    )


DEFAULT_WORKFLOWS = [
    {
        "name": "Z-Image Turbo Text To Image",
        "kind": TaskKind.image_generate,
        "comfy_workflow_key": "z-image-turbo-text-to-image",
        "credit_cost": 1,
        "description": "Generate portrait-friendly text-to-image outputs with Z-Image Turbo at 3:4, up to 1536px on the long edge.",
        "template": load_template("z_image_turbo_text_to_image_api.json"),
    },
    {
        "name": "Image Edit Assistant",
        "kind": TaskKind.image_edit,
        "comfy_workflow_key": "flux2klein-single-edit",
        "credit_cost": 1,
        "description": "Edit uploaded images with the Flux2 Klein single-edit workflow.",
        "template": load_template("flux2klein_single_edit_api.json"),
    },
]


def seed_defaults(db: Session) -> None:
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
        exists.is_active = item.get("is_active", _is_dispatchable_template(item["template"]))
        db.add(exists)

    db.flush()
    retired_workflows = list(
        db.scalars(
            select(Workflow).where(
                Workflow.comfy_workflow_key.in_(
                    RETIRED_WORKFLOW_KEYS | {"image-edit-inpaint"}
                )
            )
        )
    )
    for workflow in retired_workflows:
        workflow.is_active = False
        db.add(workflow)
    db.flush()

    purge_demo_data(db)
    seed_hidden_admin_user(db)

    db.commit()


def purge_demo_data(db: Session) -> None:
    demo_user_ids = list(
        db.scalars(select(User.id).where(User.telegram_id.in_(DEMO_TELEGRAM_IDS)))
    )
    if not demo_user_ids:
        return
    db.execute(delete(CreditLedgerEntry).where(CreditLedgerEntry.user_id.in_(demo_user_ids)))
    db.execute(delete(GenerationTask).where(GenerationTask.user_id.in_(demo_user_ids)))
    db.execute(delete(User).where(User.id.in_(demo_user_ids)))


def seed_hidden_admin_user(db: Session) -> None:
    user = db.scalar(
        select(User).where(User.username == HIDDEN_ADMIN_USERNAME).order_by(User.id.desc())
    )
    if not user:
        user = User(
            username=HIDDEN_ADMIN_USERNAME,
            display_name=HIDDEN_ADMIN_DISPLAY_NAME,
            credit_balance=0,
        )

    if not user.display_name:
        user.display_name = HIDDEN_ADMIN_DISPLAY_NAME
    user.status = UserStatus.active
    user.is_admin = True
    user.is_hidden = True
    db.add(user)
    db.flush()

    existing_grant = db.scalar(
        select(CreditLedgerEntry.id).where(
            CreditLedgerEntry.user_id == user.id,
            CreditLedgerEntry.reason == LedgerReason.admin_adjustment,
            CreditLedgerEntry.note == HIDDEN_ADMIN_GRANT_NOTE,
        )
    )
    if existing_grant is None:
        adjust_credits(
            db=db,
            user=user,
            amount=HIDDEN_ADMIN_GRANT_AMOUNT,
            reason=LedgerReason.admin_adjustment,
            note=HIDDEN_ADMIN_GRANT_NOTE,
        )
