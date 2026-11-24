"""A brief recorded reminder, with no claim that physical cleanup was verified."""

import math
from datetime import datetime, timedelta, timezone

from .models import now

MESSAGE = "读数已保存，请移开表笔并关闭万用表。"
SECONDS = 5


def start_notice(state):
    if state.mode != "live":
        return
    if not state.completion_notice_until:
        state.completion_notice_until = (
            datetime.fromisoformat(now()) + timedelta(seconds=SECONDS)
        ).isoformat()


def notice_for(state):
    if state.status != "running" or not state.completion_notice_until:
        return None
    try:
        remaining = (
            datetime.fromisoformat(state.completion_notice_until) - datetime.now(timezone.utc)
        ).total_seconds()
    except (TypeError, ValueError):
        return None
    if remaining <= 0:
        return None
    return {
        "message": MESSAGE + "提示结束后自动继续生成报告，无需拍照或确认。",
        "remaining_seconds": min(SECONDS, math.ceil(remaining)),
    }
