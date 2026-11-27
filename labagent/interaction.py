"""One rule-derived task description shared by page refresh and action responses."""
from .battery_polarity import latest_issue
from .completion_notice import notice_for
from .equipment_checks import missing_in_equipment_observation
from .step_checks import reading_progress, rule_for
from .workflow import prior_blockers, step


def describe(engine, state, decision):
    if not state.plan:
        return None
    current = step(state)
    gate = current["gate"]
    result = {"step_id": current["id"], "position": state.step + 1,
              "total": len(state.plan["steps"]), "phase": "ready", "message": "",
              "primary_action": None, "updated_at": state.updated_at}

    def show(phase, message, kind=None, label=None):
        result.update(phase=phase, message=message,
                      primary_action={"kind": kind, "label": label} if kind else None)
        return result

    if state.status == "completed":
        return show("completed", "实验已完成，测量和来源已保存，可查看结果或导出报告。", "show_results", "查看实验结果")
    if state.status == "paused":
        return show("paused", state.pause_reason or "指导已暂停，核对现场后可恢复。", "resume", "现场已核对，恢复实验")
    notice = notice_for(state)
    if notice:
        result["remaining_seconds"] = notice["remaining_seconds"]
        return show("notice", notice["message"])
    blockers = engine.blockers(state)
    if not blockers:
        if gate == "measurement" and state.automation == "agent" and state.mode == "live":
            return show("advancing", "本次读数已记录，可以移开表笔。正在进入保存步骤。", "advance", "继续")
        return show("advancing", "本步条件已通过，将自动进入下一步。", "advance", "继续下一步")
    earlier = prior_blockers(engine, state) if gate == "measurement" else []
    if earlier:
        return show("waiting_user", "；".join(earlier), "recheck", "返回准备阶段重新核对")
    if gate == "read_sop":
        return show("waiting_user", current["instruction"], "confirm_step", "已阅读要求，开始检查")
    if gate == "equipment":
        if missing := missing_in_equipment_observation(state):
            labels = "、".join(state.plan["object_labels"][key] for key in missing)
            result["waiting_for_equipment"] = True
            return show("waiting_scene", f"请将{labels}放入镜头。器材持续可见后会自动核验。",
                        "retry", "已调整，重新检查")
        observed = state.facts.get("observed_objects")
        values = observed.value if observed and isinstance(observed.value, dict) else {}
        absent = [state.plan["object_labels"][key] for key in state.plan["required_objects"]
                  if values.get(key) is not True]
        message = ("请将" + "、".join(absent) + "放入镜头，识别齐全后自动继续。" if absent else
                   "器材已看齐，正在用另一张独立画面核验（1/2）；通过后自动继续。")
        if not observed:
            message = "正在查看器材，请让本实验所需器材完整入镜。"
        phase = "waiting_scene" if observed and absent else "observing"
    elif gate == "measurement" and state.plan["measurement"]["adapter"] == "voltage":
        if state.automation == "agent" and state.mode == "live":
            ready = (decision and decision.get("tool") == "read_display"
                     and decision.get("status") == "proposed" and decision.get("ticket_valid"))
            if ready:
                return show("waiting_user", "先点击开始，随后有5秒连接表笔。定位成功后只读取一帧，识别到有效数字即记录。定位可继续尝试约10秒，本轮结束即可移开表笔。",
                            "start_reading", "开始 · 5秒后读数")
            return show("preparing_reading", "可以先移开表笔，正在准备读表工具。就绪后先点击开始，再连接表笔。")
        progress = reading_progress(engine, state)
        count = sum(not m.superseded for m in state.measurements)
        message = (f"已记录 {count}/{state.plan['measurement']['samples']} 次，"
                   f"正在核验读数（{progress['count']}/2 帧）；画面稳定后自动记录。")
        phase = "reading"
        if latest_issue(state):
            message, phase = latest_issue(state), "waiting_scene"
    elif gate == "save":
        return show("saving", "读数核验已通过，正在保存测量及图像来源。", "save_measurement", "保存并继续")
    elif (decision or {}).get("delegated_to") == "meter_live":
        message = "首次进入本步自动检查一次；未通过后显示结果，调整现场并点击重新检测后才检查下一轮。"
        phase = "observing"
    elif rule_for(state):
        proof = state.facts.get("workflow_check:" + current["id"])
        message = proof.value.get("message", current["instruction"]) if proof else current["instruction"]
        phase = "waiting_scene" if proof else "observing"
    elif gate == "measurement":
        return show("waiting_user", "本实验需要人工测量，请填写现场实际读数。", "manual_measurement", "提交读数并继续")
    else:
        return show("waiting_user", current["instruction"], "confirm_step", "本步已完成，继续")
    decision = decision or {}
    if decision.get("status") == "failed":
        return show("error", decision.get("message", "上次识别未完成，请重新检查。"), "retry", "重新检查")
    if decision.get("tool") == "ask_user" and decision.get("status") == "completed":
        return show("waiting_scene", message, "retry", "已调整现场，重新检查")
    return show(phase, message)
