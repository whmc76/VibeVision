import json

import httpx

MAX_DETAIL_CHARS = 4_000
MAX_USER_MESSAGE_CHARS = 1_500


def format_exception_details(exc: BaseException) -> str:
    lines: list[str] = []
    message = _clean_text(str(exc))
    lines.append(f"{exc.__class__.__name__}: {message}" if message else exc.__class__.__name__)

    if isinstance(exc, httpx.HTTPStatusError):
        lines.append(f"Request: {exc.request.method} {exc.request.url}")
        lines.append(
            f"Response status: {exc.response.status_code} {exc.response.reason_phrase}"
        )
        response_body = _response_body_text(exc.response)
        if response_body:
            lines.append(f"Response body: {response_body}")
    elif isinstance(exc, httpx.RequestError) and exc.request is not None:
        lines.append(f"Request: {exc.request.method} {exc.request.url}")

    if exc.__cause__:
        cause_message = _clean_text(str(exc.__cause__))
        lines.append(
            f"Caused by {exc.__cause__.__class__.__name__}: {cause_message}"
            if cause_message
            else f"Caused by {exc.__cause__.__class__.__name__}"
        )

    return "\n".join(_deduplicate(lines))


def append_error_detail(
    message: str,
    detail: str | None,
    *,
    label: str = "Details",
    task_id: int | None = None,
) -> str:
    clean_detail = _clean_text(detail or "")
    detail_parts: list[str] = []
    if task_id is not None:
        detail_parts.append(_task_id_detail(task_id, label))
    if clean_detail:
        detail_parts.append(clean_detail)
    if not detail_parts:
        return message

    clean_detail = "; ".join(detail_parts)
    budget = max(MAX_USER_MESSAGE_CHARS - len(message) - len(label) - 4, 120)
    if len(clean_detail) > budget:
        clean_detail = f"{clean_detail[: budget - 3].rstrip()}..."
    return f"{message}\n{label}: {clean_detail}"


def _response_body_text(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        body = _clean_text(response.text)
        return body or None
    return _clean_text(json.dumps(payload, ensure_ascii=False))


def _clean_text(value: str) -> str:
    return " ".join(value.split())[:MAX_DETAIL_CHARS].strip()


def _task_id_detail(task_id: int, label: str) -> str:
    return f"任务 ID: {task_id}" if label == "详细信息" else f"Task ID: {task_id}"


def _deduplicate(lines: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if not line or line in seen:
            continue
        seen.add(line)
        deduped.append(line)
    return deduped
