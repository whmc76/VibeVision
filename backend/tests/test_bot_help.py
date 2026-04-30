from app.models import MembershipTier, TaskKind, User, Workflow
from app.services.bot_help import (
    BotQuickAction,
    build_account_message,
    build_help_message,
    build_image_workflow_message,
    build_video_workflow_message,
    extract_bot_command,
    extract_bot_command_arguments,
    extract_welcome_chat_id,
    is_help_request,
    resolve_quick_action,
)
from app.services.telegram import build_bot_commands, build_remove_keyboard_markup


def build_workflows() -> list[Workflow]:
    return [
        Workflow(
            name="Z-Image Turbo Text To Image",
            kind=TaskKind.image_generate,
            comfy_workflow_key="z-image-turbo-text-to-image",
            description="Generate images from text prompts.",
            credit_cost=1,
            is_active=True,
        ),
        Workflow(
            name="Image Edit Assistant",
            kind=TaskKind.image_edit,
            comfy_workflow_key="flux2klein-single-edit",
            description="Edit uploaded images.",
            credit_cost=1,
            is_active=True,
        ),
        Workflow(
            name="Image Understanding Prompt Writer",
            kind=TaskKind.prompt_expand,
            comfy_workflow_key="prompt-expand",
            description="Understand media and expand user intent into prompts.",
            credit_cost=2,
            is_active=False,
        ),
        Workflow(
            name="Image To Video Motion",
            kind=TaskKind.video_image_to_video,
            comfy_workflow_key="image-to-video",
            description="Animate user-provided images into short videos.",
            credit_cost=10,
            is_active=True,
        ),
    ]


def test_extract_bot_command_handles_mentions_and_arguments() -> None:
    assert extract_bot_command("/help@vibevision_bot 怎么用") == "/help"
    assert extract_bot_command("/start now") == "/start"
    assert extract_bot_command("   ") is None
    assert extract_bot_command("帮我生成图片") is None


def test_extract_bot_command_arguments_returns_remaining_text() -> None:
    assert extract_bot_command_arguments("/photo@vibevision_bot 生成一张海报") == "生成一张海报"
    assert extract_bot_command_arguments("/p 生成一张海报") == "生成一张海报"
    assert extract_bot_command_arguments("/v 生成一个视频") == "生成一个视频"
    assert extract_bot_command_arguments("/video") is None
    assert extract_bot_command_arguments("生成一张海报") is None


def test_is_help_request_supports_commands_and_natural_language() -> None:
    assert is_help_request("/start")
    assert is_help_request("你能做什么？")
    assert is_help_request("支持什么能力")
    assert not is_help_request("/help")
    assert not is_help_request("生成一张写着 help 的海报")


def test_extract_welcome_chat_id_only_for_fresh_membership() -> None:
    joined_update = {
        "my_chat_member": {
            "chat": {"id": -100123456},
            "old_chat_member": {"status": "left"},
            "new_chat_member": {"status": "member"},
        }
    }
    promoted_update = {
        "my_chat_member": {
            "chat": {"id": -100123456},
            "old_chat_member": {"status": "member"},
            "new_chat_member": {"status": "administrator"},
        }
    }

    assert extract_welcome_chat_id(joined_update) == "-100123456"
    assert extract_welcome_chat_id(promoted_update) is None


def test_build_help_message_reflects_active_workflows_and_credit_costs() -> None:
    message = build_help_message(build_workflows(), include_welcome=True)

    assert "欢迎使用 VibeVision。" in message
    assert "文生图（1 积分/次）" in message
    assert "图片编辑（1 积分/次）" in message
    assert "提示词扩写" not in message
    assert "图生视频" not in message
    assert "/start 查看欢迎与能力说明" in message
    assert "/photo 或 /p <描述> 直接提交图片任务" in message
    assert "/video 或 /v <描述> 直接提交视频任务" not in message
    assert "/check 查询套餐和剩余点数" in message
    assert "/status 查看系统在线状态" in message
    assert "生图/生成图片/出图 <描述> 直接提交图片任务" in message
    assert "改图/修图/图片编辑/编辑图 <描述> 配合图片提交编辑任务" in message
    assert "生视频/生成视频/出视频 <描述> 直接提交视频任务" not in message
    assert "图生视频 <描述> 配合图片提交视频任务" not in message
    assert "English: generate image/photo/picture, edit image/photo/picture" in message
    assert "English: generate video, text to video, image/photo/picture to video" not in message


def test_resolve_quick_action_supports_keyboard_entries_and_check_command() -> None:
    assert resolve_quick_action("照片") == BotQuickAction.image
    assert resolve_quick_action("视频") == BotQuickAction.video
    assert resolve_quick_action("/photo") == BotQuickAction.image
    assert resolve_quick_action("/p") == BotQuickAction.image
    assert resolve_quick_action("/video") == BotQuickAction.video
    assert resolve_quick_action("/v") == BotQuickAction.video
    assert resolve_quick_action("查询") == BotQuickAction.query
    assert resolve_quick_action("/check") == BotQuickAction.query
    assert resolve_quick_action("/query") == BotQuickAction.query
    assert resolve_quick_action("/status") == BotQuickAction.status
    assert resolve_quick_action("系统状态") == BotQuickAction.status
    assert resolve_quick_action("图片 改成油画") is None


def test_build_image_workflow_message_lists_active_image_capabilities() -> None:
    message = build_image_workflow_message(build_workflows())

    assert "图片工作流：" in message
    assert "文生图（1 积分/次）" in message
    assert "图片编辑（1 积分/次）" in message
    assert "图生视频" not in message
    assert "推荐写法：/photo 生成一张赛博朋克风的人像海报" in message
    assert "缩写也可以：/p 生成一张赛博朋克风的人像海报" in message
    assert "关键词也可以：生图 生成一张赛博朋克风的人像海报" in message
    assert "图片编辑可以写：改图 把背景换成海边" in message
    assert "English: generate image cyberpunk portrait, edit image change background" in message


def test_build_video_workflow_message_includes_video_cost() -> None:
    message = build_video_workflow_message(build_workflows())

    assert message == "当前没有可用的视频工作流。"


def test_build_account_message_formats_tier_and_balance() -> None:
    user = User(
        membership_tier=MembershipTier.pro,
        credit_balance=88,
        total_spent_credits=32,
    )

    message = build_account_message(user)

    assert "当前身份：VIP" in message
    assert "可用点数：88" in message
    assert "累计消耗：32" in message


def test_build_remove_keyboard_markup_hides_reply_keyboard() -> None:
    assert build_remove_keyboard_markup() == {"remove_keyboard": True}


def test_build_bot_commands_has_expected_entries() -> None:
    commands = build_bot_commands()

    assert commands == [
        {"command": "photo", "description": "照片工作流"},
        {"command": "video", "description": "视频工作流"},
        {"command": "check", "description": "套餐和剩余点数"},
        {"command": "status", "description": "系统在线状态"},
        {"command": "start", "description": "欢迎与使用说明"},
    ]
