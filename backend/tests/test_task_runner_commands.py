from app.models import TaskKind
from app.services.intent import TargetOutput
from app.services.task_runner import (
    _parse_generation_request,
    _target_output_from_command,
    _task_kind_label,
)


def test_generation_commands_resolve_target_output() -> None:
    assert _target_output_from_command("/photo 生成一张海报") == TargetOutput.image
    assert _target_output_from_command("/p 生成一张海报") == TargetOutput.image
    assert _target_output_from_command("/video 生成一个短视频") == TargetOutput.video
    assert _target_output_from_command("/v 生成一个短视频") == TargetOutput.video
    assert _target_output_from_command("/check") is None


def test_generation_request_requires_known_keyword_or_command() -> None:
    assert _parse_generation_request("随便聊一句") is None


def test_generation_request_parses_image_prefixes() -> None:
    assert _parse_generation_request("/p 生成一张海报") == (
        TargetOutput.image,
        "生成一张海报",
    )
    assert _parse_generation_request("生图 一个赛博朋克女孩") == (
        TargetOutput.image,
        "一个赛博朋克女孩",
    )
    assert _parse_generation_request("改图，把背景换成海边") == (
        TargetOutput.image,
        "把背景换成海边",
    )


def test_generation_request_parses_prefix_only_as_target_selection() -> None:
    assert _parse_generation_request("生图") == (TargetOutput.image, None)
    assert _parse_generation_request("视频") == (TargetOutput.video, None)


def test_generation_request_parses_video_prefixes() -> None:
    assert _parse_generation_request("/v 生成一个短视频") == (
        TargetOutput.video,
        "生成一个短视频",
    )
    assert _parse_generation_request("图生视频 镜头缓慢推进") == (
        TargetOutput.video,
        "镜头缓慢推进",
    )


def test_task_kind_label_uses_user_facing_task_names() -> None:
    assert _task_kind_label(TaskKind.image_generate) == "图像生成任务"
    assert _task_kind_label(TaskKind.image_edit) == "图像编辑任务"
    assert _task_kind_label(TaskKind.video_text_to_video) == "视频生成任务"
    assert _task_kind_label(TaskKind.video_image_to_video) == "图生视频任务"
