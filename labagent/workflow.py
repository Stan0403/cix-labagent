"""Plan-driven verification and evidence. No physical actuator is exposed."""
from datetime import datetime, timezone

from .battery_polarity import deferred_cleanup, deferred_contact, reading_issue, requires_positive
from .completion_notice import notice_for, start_notice
from .models import Evidence, Measurement, now


def step(state):
    return state.plan["steps"][state.step]


def fresh(engine, evidence):
    if evidence is not None and (evidence.source == "vision" or
                                 (evidence.source == "replay" and evidence.reference.startswith("image:"))):
        # Visual workflow checks are validated by explicit predicates and image
        # references, not a fabricated confidence score from the VLM.
        if not evidence.reference.startswith("image:"):
            return False
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(evidence.at)).total_seconds()
            return 0 <= age <= engine.settings.evidence_ttl_seconds
        except (ValueError, TypeError):
            return False
    return engine.fresh(evidence)


def missing(engine, state):
    from .equipment_checks import verified_visual_equipment
    item = state.facts.get("objects")
    valid = item and (item.source in {"manual", "replay"} or
                      (item.source == "vision" and verified_visual_equipment(state, item)))
    found = item.value if valid and fresh(engine, item) else []
    return [x for x in state.plan["required_objects"] if x not in found]


def prior_blockers(engine, state):
    reasons = []
    for item in state.plan["steps"][:state.step]:
        if deferred_contact(state.plan, item) or deferred_cleanup(state.plan, item):
            continue
        gate = item["gate"]
        if gate == "equipment" and missing(engine, state):
            reasons.append("器材核对缺失或已过期")
        elif gate in {"read_sop", "meter_setup", "manual_check"}:
            evidence = state.checks.get(item["id"])
            if evidence is None:
                reasons.append("尚未确认：" + item["title"])
            elif gate != "read_sop" and not fresh(engine, evidence):
                reasons.append("确认已过期，请暂停后重新核对：" + item["title"])
    return reasons


def blockers(engine, state):
    if state.status == "completed":
        return []
    if state.status == "paused":
        return ["已暂停：" + state.pause_reason]
    notice = notice_for(state)
    if notice:
        return [notice["message"]]
    current = step(state)
    if deferred_contact(state.plan, current) or deferred_cleanup(state.plan, current):
        return []
    gate = current["gate"]
    if gate == "equipment":
        absent = missing(engine, state)
        return ["请核对器材：" + "、".join(state.plan["object_labels"][x] for x in absent)] if absent else []
    if gate in {"read_sop", "meter_setup", "manual_check"}:
        item = state.checks.get(current["id"])
        if item is None or (gate != "read_sop" and not fresh(engine, item)):
            from .step_checks import rule_for
            if rule_for(state):
                return ["等待本步现场视觉检查通过；请按提示调整实物或镜头，工作流会自动复查"]
            return ["请在现场完成并确认本步检查"]
        return []
    measure = state.plan.get("measurement")
    if gate == "measurement":
        reasons = prior_blockers(engine, state)
        values = [m for m in state.measurements if not m.superseded]
        if len(values) < measure["samples"]:
            reasons.append(f"需确认 {measure['samples']} 次测量，目前 {len(values)} 次")
        return reasons
    if gate == "save":
        return [] if state.measurements and all(m.saved for m in state.measurements if not m.superseded) else ["请核对并保存本次测量"]
    if gate == "report" and measure:
        return [] if sum(m.saved and not m.superseded for m in state.measurements) >= measure["samples"] else ["尚无足够的已保存真实测量"]
    return []


def expire_candidates(state):
    for candidate in state.candidates:
        if candidate.status == "pending":
            candidate.status = "expired"


def add_measurement(engine, state, value, source, confidence=1, reference="", candidate_id="", original_value=None):
    if state.status != "running" or step(state)["gate"] != "measurement":
        return {"accepted": False, "message": "只有当前实验的测量阶段才能录入读数"}
    reasons = prior_blockers(engine, state)
    if reasons:
        return {"accepted": False, "message": "；".join(reasons)}
    spec = state.plan["measurement"]
    issue = reading_issue(state.plan, value)
    if issue:
        state.risk = issue
        return {"accepted": False, "message": issue}
    if not spec["min_value"] <= value <= spec["max_value"]:
        state.risk = f"数值 {value:g} 超出计划范围，请核对量程、符号和实验对象"
        return {"accepted": False, "message": state.risk}
    if state.mode == "live" and source == "replay":
        return {"accepted": False, "message": "测试回放不能写入真实测量"}
    if len([m for m in state.measurements if not m.superseded]) >= 100:
        return {"accepted": False, "message": "本会话测量数量已达上限，请新建实验"}
    state.measurements.append(Measurement(value=value, unit=spec["unit"], source="replay" if state.mode == "replay" else source,
                                         at=now(), confidence=confidence, reference=reference, candidate_id=candidate_id,
                                         original_value=original_value))
    state.risk = "读数为负，请核对表笔极性；保留实际符号，不自动取绝对值。" if spec["adapter"] == "voltage" and value < 0 else ""
    return {"accepted": True, "message": "已确认一条测量，保留原图和修正来源。" + state.risk}


def apply(engine, state, action, source="manual"):
    def reject(message):
        return {"accepted": False, "message": message}
    kind = action.kind
    if source == "replay" and state.mode != "replay":
        return reject("回放事件不能修改真实实验")
    if kind == "pause":
        if state.status == "completed":
            return reject("实验已完成")
        state.status, state.pause_reason = "paused", action.note or "现场人员暂停，请确认当前操作"
        return {"accepted": True, "message": "已暂停指导；本系统不控制物理设备。"}
    if kind == "resume":
        if state.status != "paused" or not action.confirmed:
            return reject("请由现场人员核对后确认恢复")
        state.status, state.pause_reason = "running", ""
        if (requires_positive(state.plan) and
                (step(state)["gate"] == "save" or deferred_cleanup(state.plan, step(state))) and
                any(m.saved and not m.superseded for m in state.measurements)):
            start_notice(state)
        return {"accepted": True, "message": "已恢复；原有步骤条件仍需满足"}
    if kind == "invalidate_wiring":
        state.completion_notice_until = ""
        expire_candidates(state)
        state.checks = {k: v for k, v in state.checks.items() if k == state.plan["steps"][0]["id"]}
        for m in state.measurements:
            if not m.saved:
                m.superseded = True
        state.facts.pop("objects", None)
        state.facts.pop("meter_range", None)
        state.facts.pop("equipment_stability", None)
        state.facts.pop("observed_objects", None)
        state.facts.pop("object_boxes", None)
        for key in list(state.facts):
            if key.startswith("workflow_check:") or key == "reading_stability":
                state.facts.pop(key)
        state.step = 1
        state.status, state.pause_reason = "paused", "现场接线或档位变化，请重新核对后恢复"
        return {"accepted": True, "message": state.pause_reason}
    if kind == "remeasure":
        if state.status == "paused":
            return reject("请先确认恢复，重测不能绕过暂停")
        index = next((i for i, s in enumerate(state.plan["steps"]) if s["gate"] == "measurement"), None)
        if index is None or state.step < index:
            return reject("尚未到达测量阶段")
        for m in state.measurements:
            m.superseded = True
        expire_candidates(state)
        state.completion_notice_until = ""
        state.facts.pop("reading_stability", None)
        state.step, state.status = index, "running"
        return {"accepted": True, "message": "已返回测量，旧记录保留并标为被替代"}
    if state.status != "running":
        return reject("实验已暂停或完成")
    current = step(state)
    gate = current["gate"]
    if kind in {"advance", "report"}:
        if kind == "report" and gate != "report":
            return reject("尚未到报告阶段")
        reasons = blockers(engine, state)
        if reasons:
            return reject("；".join(reasons))
        if gate == "report":
            state.status = "completed"
            return {"accepted": True, "message": "实验已完成，报告与全部证据可以导出"}
        state.step += 1
        deferred = []
        omitted_cleanup = []
        while deferred_contact(state.plan, step(state)) or deferred_cleanup(state.plan, step(state)):
            (deferred if deferred_contact(state.plan, step(state)) else omitted_cleanup).append(step(state)["id"])
            state.step += 1
        expire_candidates(state)
        message = "已进入：" + step(state)["title"]
        if deferred:
            message += "。电池接触与极性检查已合并到读数环节。"
        return {"accepted": True, "message": message, "deferred_contact_steps": deferred,
                "omitted_cleanup_steps": omitted_cleanup}
    if not action.confirmed:
        return reject("需要现场人员明确确认，模型不能代替")
    if kind in {"confirm_step", "read_sop", "confirm_meter_setup"}:
        if gate not in {"manual_check", "read_sop", "meter_setup"}:
            return reject("当前步骤请使用对应的器材、测量或保存操作")
        if kind == "read_sop" and gate != "read_sop":
            return reject("当前不是阅读要求步骤")
        if gate == "meter_setup":
            if action.meter_range not in {"dc_2v", "dc_20v", "dc_200mv"}:
                return reject("请明确选择现场核对的直流电压档位")
            state.facts["meter_range"] = Evidence(value=action.meter_range, source=source)
        state.checks[current["id"]] = Evidence(value=True, source="replay" if state.mode == "replay" else source, reference=action.note)
        return {"accepted": True, "message": "本步现场确认已记录；可验证后进入下一步"}
    if kind == "confirm_equipment":
        if gate != "equipment" or set(action.objects) - set(state.plan["required_objects"]):
            return reject("请在器材检查阶段按本实验清单核对")
        state.facts["objects"] = Evidence(value=sorted(set(action.objects)), source="replay" if state.mode == "replay" else source)
        absent = missing(engine, state)
        return {"accepted": True, "message": "仍有器材缺失，暂不推进" if absent else "器材齐全，检查通过"}
    if kind == "manual_measurement":
        if action.value is None:
            return reject("请输入实际测量值")
        return add_measurement(engine, state, action.value, source)
    if kind in {"confirm_candidate", "reject_candidate"}:
        candidate = next((x for x in state.candidates if x.id == action.candidate_id), None)
        if candidate is None or candidate.status != "pending" or candidate.step_id != current["id"]:
            return reject("候选已处理、已失效或不属于当前步骤，请重新拍照")
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(candidate.at)).total_seconds()
        except ValueError:
            age = 99999
        if not 0 <= age <= engine.settings.evidence_ttl_seconds:
            candidate.status = "expired"
            return reject("候选读数已过期，请重新拍照")
        if kind == "reject_candidate":
            candidate.status = "rejected"
            return {"accepted": True, "message": "已拒绝候选，请重新拍照；拒绝记录保留"}
        value = action.value if action.value is not None else candidate.value
        if value is None:
            return reject("读数尚不明确，请核对图片后输入实际数值，或重新拍照")
        result = add_measurement(engine, state, value, "vision_confirmed", reference=candidate.reference,
                                 candidate_id=candidate.id, original_value=candidate.value)
        if result["accepted"]:
            candidate.status, candidate.confirmed_value = "confirmed", value
            candidate.confirmation_method = "manual"
        return result
    if kind == "save_measurement":
        if gate != "save":
            return reject("请先在测量阶段确认读数，再进入保存阶段")
        active = [m for m in state.measurements if not m.superseded]
        if not active:
            return reject("没有可保存的测量")
        for m in active:
            m.saved = True
        if requires_positive(state.plan):
            start_notice(state)
        return {"accepted": True, "message": f"已保存 {len(active)} 条测量，来源可追溯"}
    return reject("此操作不适用于当前文档计划")
