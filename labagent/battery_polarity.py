"""Battery polarity is checked from signed readings, never invented visual contact evidence."""

import math
import re


def requires_positive(plan):
    if not plan or (plan.get("measurement") or {}).get("adapter") != "voltage":
        return False
    policy = plan["measurement"].get("polarity_check", "auto")
    if policy != "auto":
        return policy == "positive"
    labels = " ".join(plan.get("object_labels", {}).values()).lower()
    return "电池" in labels or "battery" in labels


def contact_step(item):
    if item.get("gate") != "manual_check" or item.get("visual_check") == "manual":
        return False
    if item.get("visual_check") == "battery_contact":
        return True
    if item.get("visual_check", "auto") != "auto":
        return False
    text = item.get("title", "") + item.get("instruction", "")
    if any(word in text for word in ("移开表笔", "关闭万用表", "收尾", "断开表笔")):
        return False
    return any(word in text for word in ("表笔", "红正黑负")) and any(
        word in text for word in ("正极", "负极", "红正黑负", "接触电池")
    )


def deferred_contact(plan, item):
    """Keep old session plans and event indices intact, without a blocking contact VLM call."""
    if not requires_positive(plan) or not contact_step(item):
        return False
    steps = plan.get("steps", [])
    contact_index = next((i for i, s in enumerate(steps) if s["id"] == item["id"]), None)
    measure_index = next((i for i, s in enumerate(steps) if s["gate"] == "measurement"), None)
    return contact_index is not None and measure_index is not None and contact_index < measure_index


def reading_issue(plan, value):
    if value is None or not requires_positive(plan):
        return ""
    if value < 0 or (value == 0 and math.copysign(1, value) < 0):
        kind = "负零" if value == 0 else "负数"
        return f"读数为{kind}（{value:g} V），请检查红表笔是否接电池正极、黑表笔是否接负极；调整后重新读数，本次不计入测量。"
    if value == 0:
        return (
            "读数为零，暂不能确认有效电压；请检查表笔接触、档位和电池状态，调整后重新读数，本次不计入测量。"
        )
    return ""


def deferred_cleanup(plan, item):
    if (
        not requires_positive(plan)
        or item.get("gate") != "manual_check"
        or item.get("visual_check") == "manual"
    ):
        return False
    text = item.get("title", "") + item.get("instruction", "")
    cleanup = item.get("visual_check") == "battery_cleanup" or all(
        word in text for word in ("移开", "表笔", "关闭", "万用表")
    )
    steps = plan.get("steps", [])
    index = next((i for i, s in enumerate(steps) if s["id"] == item["id"]), None)
    measure = next((i for i, s in enumerate(steps) if s["gate"] == "measurement"), None)
    return cleanup and index is not None and measure is not None and index > measure


def latest_issue(state):
    if not state.plan or not state.candidates:
        return ""
    candidate = state.candidates[-1]
    if candidate.status == "confirmed" and candidate.confirmation_method == "single_frame":
        return ""
    current = state.plan["steps"][state.step]
    if current["gate"] != "measurement" or candidate.step_id != current["id"]:
        return ""
    return reading_issue(state.plan, candidate.value)


def independent_captures(first, second):
    """Different image content or two distinct, explicitly identified camera captures."""
    if first.id == second.id:
        return False
    camera_ids = all(re.fullmatch(r"capture_[0-9a-f]{32}", item.id) for item in (first, second))
    return camera_ids or first.reference != second.reference
