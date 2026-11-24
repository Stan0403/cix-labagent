"""Fixed, evidence-backed visual workflow adapters. No LLM action planner."""
import math
from datetime import datetime, timezone

from .battery_polarity import deferred_cleanup, deferred_contact, independent_captures, reading_issue
from .models import Evidence

RULES = {
    "meter_setup": {
        "title": "核对直流档位与表笔插孔",
        "checks": {"black_in_com": "黑表笔插入 COM 孔", "red_in_voltage": "红表笔插入 V/Ω 电压孔",
                   "dc_voltage_mode": "使用直流电压档", "suitable_range": "量程适合本次测量"},
        "hint": "请让旋钮、档位文字和两个表笔插孔同时清晰入镜；不要遮挡插孔。",
    },
    "battery_contact": {
        "title": "检查表笔接触与极性",
        "checks": {"red_on_positive": "红表笔接触电池正极", "black_on_negative": "黑表笔接触电池负极",
                   "no_short": "两表笔金属部分未相碰或短接电池"},
        "hint": "请让电池两端、红黑表笔金属尖端及接触位置清晰入镜，并保持稳定。",
    },
    "battery_cleanup": {
        "title": "检查移开表笔与关闭万用表",
        "checks": {"probes_removed": "红黑表笔均已离开电池两端", "meter_off": "万用表已关闭"},
        "hint": "请移开电池两端的表笔，关闭万用表，让电池、表笔和仪表旋钮入镜。",
    },
}
RANGES = {"dc_200mv": .2, "dc_2v": 2., "dc_20v": 20.}


def rule_for(state):
    if not state.plan:
        return None
    current = state.plan["steps"][state.step]
    if deferred_contact(state.plan, current) or deferred_cleanup(state.plan, current):
        return None
    explicit = current.get("visual_check", "auto")
    if explicit == "manual":
        return None
    spec = state.plan.get("measurement") or {}
    if spec.get("adapter") != "voltage":
        return None
    if current["gate"] == "meter_setup":
        return "meter_setup"
    if current["gate"] != "manual_check":
        return None
    names = " ".join(state.plan.get("object_labels", {}).values()).lower()
    if "电池" not in names and "battery" not in names:
        return None
    if explicit in {"battery_contact", "battery_cleanup"}:
        return explicit
    text = current["title"] + current["instruction"]
    if any(word in text for word in ("移开表笔", "关闭万用表", "收尾", "断开表笔")):
        return "battery_cleanup"
    if any(word in text for word in ("正极", "负极", "红正黑负", "接触电池")):
        return "battery_contact"
    return None


def result_for(rule, response, spec):
    """Only actual boolean values satisfy a check; model confidence is not proof."""
    raw = response.get("checks") if isinstance(response, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    checks = {key: raw.get(key) if type(raw.get(key)) is bool else None for key in RULES[rule]["checks"]}
    meter_range = response.get("meter_range") if isinstance(response, dict) else None
    if not isinstance(meter_range, str) or meter_range not in RANGES:
        meter_range = None
    if rule == "meter_setup":
        limit = RANGES.get(meter_range)
        required = max(abs(spec["min_value"]), abs(spec["max_value"]))
        checks["suitable_range"] = limit >= required if limit is not None else None
    failed = [RULES[rule]["checks"][key] for key, value in checks.items() if value is False]
    unknown = [RULES[rule]["checks"][key] for key, value in checks.items() if value is None]
    passed = not failed and not unknown
    message = "本步视觉检查通过，将自动进入下一步。" if passed else ""
    if failed:
        message += "尚未满足：" + "、".join(failed) + "。"
    if unknown:
        message += "看不清或无法判断：" + "、".join(unknown) + "。"
    if not passed:
        message += RULES[rule]["hint"] + "调整后工作流会自动复查。"
    return {"rule": rule, "title": RULES[rule]["title"], "checks": checks,
            "labels": RULES[rule]["checks"], "passed": passed, "meter_range": meter_range,
            "message": message, "backend": response.get("backend", "")}


def record_result(state, result, reference):
    current = state.plan["steps"][state.step]
    key = "workflow_check:" + current["id"]
    previous = state.facts.get(key)
    result["attempt"] = (previous.value.get("attempt", 0) if previous else 0) + 1
    source = "replay" if state.mode == "replay" else "vision"
    state.facts[key] = Evidence(value=result, source=source, reference=reference, confidence=0)
    if result["passed"]:
        state.checks[current["id"]] = Evidence(value=True, source=source, reference=reference, confidence=0)
        if result["rule"] == "meter_setup":
            state.facts["meter_range"] = Evidence(value=result["meter_range"], source=source, reference=reference, confidence=0)
    else:
        state.checks.pop(current["id"], None)
        if result["rule"] == "meter_setup":
            state.facts.pop("meter_range", None)


def reading_progress(engine, state):
    """Two fresh, independently captured readings; no rejected or previous sample reuse."""
    current = state.plan["steps"][state.step]
    consumed = {cid for m in state.measurements for cid in m.supporting_candidate_ids + [m.candidate_id]}
    samples = []
    for candidate in reversed(state.candidates):
        if candidate.step_id != current["id"] or candidate.id in consumed:
            break
        if candidate.status in {"rejected", "confirmed"} or candidate.value is None:
            break
        if reading_issue(state.plan, candidate.value):
            break
        spec = state.plan["measurement"]
        if not spec["min_value"] <= candidate.value <= spec["max_value"]:
            break
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(candidate.at)).total_seconds()
        except ValueError:
            break
        if not 0 <= age <= engine.settings.evidence_ttl_seconds:
            break
        samples.append(candidate)
        if len(samples) == 2:
            break
    stable = False
    if len(samples) == 2:
        newest, earlier = samples
        delta = abs((datetime.fromisoformat(newest.at)-datetime.fromisoformat(earlier.at)).total_seconds())
        stable = (delta >= 1 and independent_captures(newest, earlier) and newest.meter_range == earlier.meter_range
                  and math.isclose(newest.value, earlier.value, rel_tol=0, abs_tol=.02))
    return {"count": len(samples), "required": 2, "tolerance_v": .02, "stable": stable,
            "candidate_ids": [c.id for c in reversed(samples)]}


def confirm_stable_reading(engine, state):
    from .workflow import add_measurement
    progress = reading_progress(engine, state)
    if not progress["stable"]:
        return False
    candidate = state.candidates[-1]
    result = add_measurement(engine, state, candidate.value, "vision_workflow", confidence=0,
                             reference=candidate.reference, candidate_id=candidate.id, original_value=candidate.value)
    if result["accepted"]:
        candidate.status, candidate.confirmed_value = "confirmed", candidate.value
        candidate.confirmation_method = "workflow_stability"
        state.measurements[-1].supporting_candidate_ids = progress["candidate_ids"]
        state.facts["reading_stability"] = Evidence(value=progress, source="replay" if state.mode == "replay" else "vision",
                                                    reference=candidate.reference, confidence=0)
    return result["accepted"]
