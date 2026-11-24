"""Fixed workflow routing by reviewed step type and actual evidence."""

from .battery_polarity import latest_issue
from .completion_notice import notice_for
from .step_checks import RULES, reading_progress, rule_for


def decide(engine, state):
    if not state.plan:
        return None
    current = state.plan["steps"][state.step]
    result = {"controller": "workflow", "goal": current["title"], "tool": None, "autorun": False,
              "retry_after_seconds": 8, "message": "", "visual_rule": rule_for(state), "checks": None}
    previous = state.facts.get("workflow_check:" + current["id"])
    if previous:
        result["checks"] = previous.value
    if state.status == "completed":
        result["message"] = "实验目标已完成，报告已准备好，可导出。"
        return result
    if state.status == "paused":
        result["message"] = "已暂停：" + state.pause_reason
        return result
    notice = notice_for(state)
    if notice:
        result.update(message=notice["message"], retry_after_seconds=notice["remaining_seconds"])
        return result
    blockers = engine.blockers(state)
    gate = current["gate"]
    if not blockers:
        result.update(tool="report" if gate == "report" else "advance", autorun=True,
                      message="当前条件已满足，我将更新进度。" if gate != "report" else "测量与操作记录齐备，我将完成实验报告。")
        return result
    if gate == "equipment":
        observed = state.facts.get("observed_objects")
        labels = state.plan["object_labels"]
        if observed:
            values = observed.value
            absent = [labels[k] for k in state.plan["required_objects"] if values.get(k) is False]
            unknown = [labels[k] for k in state.plan["required_objects"] if values.get(k) is None]
            if not absent and not unknown:
                result["message"] = "已观察到所需器材，正在采集另一张独立画面核对；通过后自动继续。"
            pieces = []
            if absent:
                pieces.append("还未看到：" + "、".join(absent) + "。请补充到镜头内。")
            if unknown:
                pieces.append("暂不能确认：" + "、".join(unknown) + "。请调整摆放或镜头。")
            if pieces:
                result["message"] = "".join(pieces) + "我会继续观察，也可展开人工修正。"
        else:
            result["message"] = "进入器材检查。我将读取摄像头画面，调用本地视觉模型，检查还缺少哪些器材。"
        result.update(tool="inspect_equipment", autorun=True)
    elif gate == "measurement":
        pending = next((x for x in reversed(state.candidates) if x.status == "pending"), None)
        if state.plan["measurement"]["adapter"] == "voltage":
            progress = reading_progress(engine, state)
            result["stability"] = progress
            message = "将自动定位显示屏并连续读取；两次独立采集间隔至少1秒，读数相差不超过0.02 V才自动记录，两次都是1.34 V也能通过。"
            if pending:
                message = (f"本次读数 {pending.value:g} V，继续采集独立画面检查稳定性。" if pending.value is not None
                           else "本次读数不明确，请调整屏幕角度和光照，工作流将自动重拍。")
            message = latest_issue(state) or message
            result.update(tool="read_display", autorun=True, message=message)
        else:
            result["message"] = "本实验没有对应的自动测量适配器。请按文档测量后录入实际数值。"
    elif gate == "save":
        result.update(tool="save_measurement", autorun=True, message="读数已通过稳定性检查或人工核对，工作流将自动保存，再检查收尾。")
    elif result["visual_rule"]:
        rule = result["visual_rule"]
        message = previous.value["message"] if previous else RULES[rule]["title"] + "：" + RULES[rule]["hint"]
        result.update(tool="inspect_step", autorun=True, message=message)
    else:
        result["message"] = current["instruction"] + " 本步暂无对应视觉检查规则，请完成后手动确认。"
    return result
