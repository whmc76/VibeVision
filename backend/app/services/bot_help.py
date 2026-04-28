from collections.abc import Sequence
from enum import StrEnum

from app.models import MembershipTier, TaskKind, User, Workflow


class BotQuickAction(StrEnum):
    image = "image"
    video = "video"
    query = "query"


_HELP_COMMANDS = {"/start"}
_HELP_EXACT_MATCHES = {
    "help",
    "帮助",
    "使用说明",
    "功能说明",
    "功能介绍",
    "使用帮助",
    "帮助说明",
}
_HELP_PREFIXES = (
    "help",
    "帮助",
    "怎么用",
    "如何使用",
    "使用说明",
    "功能说明",
    "功能介绍",
    "支持什么",
    "支持哪些",
    "你会什么",
    "你能做什么",
    "你可以做什么",
    "能做什么",
    "what can you do",
    "how to use",
)
_HELP_CONTAINS = (
    "支持什么能力",
    "支持哪些能力",
    "有哪些功能",
    "有什么功能",
    "能帮我做什么",
)
_IMAGE_COMMANDS = {"/photo", "/p", "/image", "/photos", "/images"}
_VIDEO_COMMANDS = {"/video", "/v", "/videos"}
_QUERY_COMMANDS = {"/check", "/query", "/account", "/credits", "/balance", "/vip"}
_QUICK_ACTION_EXACT_MATCHES = {
    BotQuickAction.image: {"照片", "图片"},
    BotQuickAction.video: {"视频"},
    BotQuickAction.query: {
        "查询",
        "积分",
        "点数",
        "余额",
        "vip",
        "会员",
        "账户",
        "账号",
        "我的积分",
        "我的点数",
        "我的vip",
        "我的会员",
    },
}
_CAPABILITY_ORDER = (
    TaskKind.image_generate,
    TaskKind.image_edit,
    TaskKind.video_text_to_video,
    TaskKind.video_image_to_video,
    TaskKind.prompt_expand,
)
_IMAGE_MENU_KINDS = (TaskKind.image_generate, TaskKind.image_edit)
_CAPABILITY_COPY = {
    TaskKind.image_generate: (
        "文生图",
        "直接描述想生成的画面、风格、构图或光线。",
        "生成一张赛博朋克风的人像海报",
    ),
    TaskKind.image_edit: (
        "图片编辑",
        "发送图片并说明怎么改，比如换衣服、改背景、换风格、加减元素。",
        "把这张照片背景改成海边日落，保留人物",
    ),
    TaskKind.video_text_to_video: (
        "文生视频",
        "直接描述想生成的视频内容、动作、镜头、节奏和风格。",
        "生成一个霓虹街头雨夜慢镜头短视频",
    ),
    TaskKind.video_image_to_video: (
        "图生视频",
        "发送图片并描述动作、镜头或运镜效果。",
        "让这张角色图做一个缓慢推镜头的视频",
    ),
    TaskKind.prompt_expand: (
        "提示词扩写",
        "帮你扩写提示词，或根据图片整理成更完整的生成提示词。",
        "帮我把这张图整理成一段英文提示词",
    ),
}
_MEMBERSHIP_TIER_LABELS = {
    MembershipTier.free: "游客",
    MembershipTier.starter: "正式会员",
    MembershipTier.pro: "VIP",
    MembershipTier.studio: "SVIP",
}
_SUBSCRIPTION_PLAN_LABELS = {
    "monthly": "月度订阅",
    "premium": "高级订阅",
}


def extract_bot_command(text: str | None) -> str | None:
    if not text:
        return None

    tokens = text.strip().split(maxsplit=1)
    if not tokens:
        return None

    first_token = tokens[0]
    if not first_token.startswith("/"):
        return None

    command = first_token.split("@", 1)[0].lower()
    return command or None


def extract_bot_command_arguments(text: str | None) -> str | None:
    if not text:
        return None

    tokens = text.strip().split(maxsplit=1)
    if len(tokens) < 2:
        return None
    if not tokens[0].startswith("/"):
        return None

    arguments = tokens[1].strip()
    return arguments or None


def is_start_command(text: str | None) -> bool:
    return extract_bot_command(text) == "/start"


def is_help_request(text: str | None) -> bool:
    command = extract_bot_command(text)
    if command in _HELP_COMMANDS:
        return True

    normalized = _normalize_text(text)
    if not normalized:
        return False

    if normalized in _HELP_EXACT_MATCHES:
        return True

    if len(normalized) <= 80 and any(normalized.startswith(prefix) for prefix in _HELP_PREFIXES):
        return True

    return len(normalized) <= 80 and any(token in normalized for token in _HELP_CONTAINS)


def resolve_quick_action(text: str | None) -> BotQuickAction | None:
    command = extract_bot_command(text)
    if command in _IMAGE_COMMANDS:
        return BotQuickAction.image
    if command in _VIDEO_COMMANDS:
        return BotQuickAction.video
    if command in _QUERY_COMMANDS:
        return BotQuickAction.query

    normalized = _normalize_text(text)
    if not normalized:
        return None

    for action, matches in _QUICK_ACTION_EXACT_MATCHES.items():
        if normalized in matches:
            return action
    return None


def extract_welcome_chat_id(update: dict) -> str | None:
    payload = update.get("my_chat_member")
    if not isinstance(payload, dict):
        return None

    new_status = str((payload.get("new_chat_member") or {}).get("status") or "")
    old_status = str((payload.get("old_chat_member") or {}).get("status") or "")
    if new_status not in {"member", "administrator"}:
        return None
    if old_status and old_status not in {"left", "kicked"}:
        return None

    chat_id = (payload.get("chat") or {}).get("id")
    if chat_id is None:
        return None
    return str(chat_id)


def build_help_message(
    workflows: Sequence[Workflow] | None = None,
    *,
    include_welcome: bool = False,
) -> str:
    active_capabilities = _active_capabilities(workflows)
    active_kinds = active_capabilities.keys() if active_capabilities else _CAPABILITY_ORDER

    lines: list[str] = []
    if include_welcome:
        lines.append("欢迎使用 VibeVision。")
        lines.append("")

    lines.append("我目前支持这些能力：")
    for index, kind in enumerate(active_kinds, start=1):
        title, description, _example = _CAPABILITY_COPY[kind]
        credit_cost = active_capabilities.get(kind)
        credit_label = f"（{credit_cost} 积分/次）" if credit_cost is not None else ""
        lines.append(f"{index}. {title}{credit_label}：{description}")

    lines.append("")
    lines.append("示例：")
    for kind in active_kinds:
        _title, _description, example = _CAPABILITY_COPY[kind]
        lines.append(f"- {example}")

    lines.append("")
    lines.append("命令：")
    lines.append("/start 查看欢迎与能力说明")
    lines.append("/photo 或 /p <描述> 直接提交图片任务")
    lines.append("/video 或 /v <描述> 直接提交视频任务")
    lines.append("/check 查询套餐和剩余点数")
    lines.append("")
    lines.append("发送图片时，最好附上文字要求，这样我能更准确地理解你的意图。")
    return "\n".join(lines)


def build_image_workflow_message(workflows: Sequence[Workflow] | None = None) -> str:
    active_capabilities = _active_capabilities(workflows)
    image_kinds = [kind for kind in _IMAGE_MENU_KINDS if kind in active_capabilities]
    if not image_kinds:
        image_kinds = list(_IMAGE_MENU_KINDS)

    lines = ["图片工作流："]
    for index, kind in enumerate(image_kinds, start=1):
        _title, description, _example = _CAPABILITY_COPY[kind]
        lines.append(f"{index}. {_format_capability_title(kind, active_capabilities)}：{description}")

    lines.append("")
    lines.append("示例：")
    for kind in image_kinds:
        _title, _description, example = _CAPABILITY_COPY[kind]
        lines.append(f"- {example}")

    lines.append("")
    lines.append("推荐写法：/photo 生成一张赛博朋克风的人像海报")
    lines.append("缩写也可以：/p 生成一张赛博朋克风的人像海报")
    lines.append("")
    lines.append("直接发送文字描述，或发送图片并附上修改要求。")
    return "\n".join(lines)


def build_video_workflow_message(workflows: Sequence[Workflow] | None = None) -> str:
    active_capabilities = _active_capabilities(workflows)
    video_kinds = [
        kind
        for kind in (TaskKind.video_text_to_video, TaskKind.video_image_to_video)
        if kind in active_capabilities
    ]
    if not video_kinds:
        video_kinds = [TaskKind.video_text_to_video, TaskKind.video_image_to_video]

    lines = ["视频工作流："]
    for index, kind in enumerate(video_kinds, start=1):
        _title, description, _example = _CAPABILITY_COPY[kind]
        lines.append(f"{index}. {_format_capability_title(kind, active_capabilities)}：{description}")

    lines.append("")
    lines.append("示例：")
    for kind in video_kinds:
        _title, _description, example = _CAPABILITY_COPY[kind]
        lines.append(f"- {example}")

    lines.append("")
    lines.append("推荐写法：/video 生成一个霓虹街头雨夜慢镜头短视频")
    lines.append("缩写也可以：/v 生成一个霓虹街头雨夜慢镜头短视频")
    lines.append("")
    lines.append("请先明确选择视频，再发送文字描述；做图生视频时附上图片。")
    return "\n".join(lines)


def build_account_message(user: User) -> str:
    tier_label = _MEMBERSHIP_TIER_LABELS.get(user.membership_tier, user.membership_tier.value)
    paid_balance = int(user.credit_balance or 0)
    daily_bonus_balance = int(user.daily_bonus_balance or 0)
    total_balance = paid_balance + daily_bonus_balance
    recharge_usd = int(user.total_recharge_usd_cents or 0) / 100
    plan_label = _SUBSCRIPTION_PLAN_LABELS.get(user.subscription_plan or "", "无")
    lines = [
        "账户信息：",
        f"当前身份：{tier_label}",
        f"订阅：{plan_label}",
        f"可用点数：{total_balance}",
        f"永久点数：{paid_balance}",
        f"今日赠送：{daily_bonus_balance}",
        f"累计充值：${recharge_usd:.2f}",
        f"累计消耗：{user.total_spent_credits}",
    ]
    return "\n".join(lines)


def _active_capabilities(workflows: Sequence[Workflow] | None) -> dict[TaskKind, int]:
    if not workflows:
        return {}

    active_capabilities: dict[TaskKind, int] = {}
    for workflow in workflows:
        if not workflow.is_active or workflow.kind in active_capabilities:
            continue
        active_capabilities[workflow.kind] = workflow.credit_cost

    return {
        kind: active_capabilities[kind]
        for kind in _CAPABILITY_ORDER
        if kind in active_capabilities
    }


def _format_capability_title(kind: TaskKind, active_capabilities: dict[TaskKind, int]) -> str:
    title, _description, _example = _CAPABILITY_COPY[kind]
    credit_cost = active_capabilities.get(kind)
    credit_label = f"（{credit_cost} 积分/次）" if credit_cost is not None else ""
    return f"{title}{credit_label}"


def _normalize_text(text: str | None) -> str:
    return " ".join((text or "").strip().lower().split())
