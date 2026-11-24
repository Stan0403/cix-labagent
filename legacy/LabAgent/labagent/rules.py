"""Deterministic gates. Neither a language model nor a vision model can attest safety."""
import json
from datetime import datetime, timezone

from .config import ROOT, Settings
from .models import Action, Evidence, Measurement, State, now


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.spec = json.loads((ROOT / "configs" / "experiment.json").read_text(encoding="utf-8"))
        if len(self.spec["steps"]) != 9:
            raise ValueError("This workflow expects the nine documented experiment steps")

    def fresh(self, evidence: Evidence | None) -> bool:
        if evidence is None or evidence.confidence < self.settings.min_confidence:
            return False
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(evidence.at)).total_seconds()
            return 0 <= age <= self.settings.evidence_ttl_seconds
        except (ValueError, TypeError):
            return False

    def fact(self, state: State, name: str, value=True, safety=False) -> bool:
        evidence = state.facts.get(name)
        return bool(
            self.fresh(evidence) and evidence.value == value
            and (not safety or evidence.source in {"manual", "replay"})
        )

    def missing_objects(self, state: State) -> list[str]:
        evidence = state.facts.get("objects")
        found = evidence.value if self.fresh(evidence) else []
        return [item for item in self.spec["required_objects"] if item not in found]

    def active_measurement(self, state: State) -> Measurement | None:
        return next((m for m in reversed(state.measurements) if not m.superseded), None)

    def measurement_ready(self, state: State) -> bool:
        m = self.active_measurement(state)
        return bool(m and self.fresh(Evidence(value=m.value, source=m.source, at=m.at,
                                              confidence=m.confidence)))

    def setup_blockers(self, state: State, powered=True) -> list[str]:
        result = []
        if self.missing_objects(state):
            result.append("器材证据缺失、过期或有遗漏，请重新检查器材")
        if not self.fact(state, "connected", safety=True):
            result.append("缺少有效的人工连接确认")
        if not self.fact(state, "wiring", safety=True):
            result.append("缺少有效的人工接线复核")
        if powered and not self.fact(state, "power", "on", safety=True):
            result.append("未确认上电，或上电确认已过期")
        return result

    def blockers(self, state: State) -> list[str]:
        if state.status == "completed":
            return []
        if state.status == "paused":
            return ["实验已暂停：" + state.pause_reason]
        step = state.step
        if step == 0:
            return [] if "sop" in state.facts else ["请阅读并确认实验 SOP"]
        if step == 1:
            return [] if self.fact(state, "power", "off", safety=True) else ["必须人工确认断电"]
        if step == 2:
            return (["缺少器材：" + "、".join(self.spec["object_labels"][x]
                    for x in self.missing_objects(state))] if self.missing_objects(state) else [])
        if step == 3:
            result = []
            if not self.fact(state, "power", "off", safety=True):
                result.append("接线前必须重新确认断电")
            if self.missing_objects(state):
                result.append("器材检查未通过或已过期")
            if not self.fact(state, "connected", safety=True):
                result.append("请人工确认传感器连接完成")
            return result
        if step == 4:
            result = []
            if not self.fact(state, "power", "off", safety=True):
                result.append("复核接线前必须确认断电")
            if not self.fact(state, "connected", safety=True):
                result.append("连接确认缺失或已过期")
            if not self.fact(state, "wiring", safety=True):
                result.append("需要人工复核接线、极性和仪表档位")
            return result
        if step in {5, 6}:
            result = self.setup_blockers(state)
            if step == 6 and not self.measurement_ready(state):
                result.append("缺少有效且足够清晰的近期电压读数")
            return result
        if step == 7:
            m = self.active_measurement(state)
            return [] if m and m.saved else ["请先核对并保存本次测量"]
        return [] if any(m.saved and not m.superseded for m in state.measurements) else ["没有有效的已保存测量"]

    def apply(self, state: State, action: Action, source="manual") -> dict:
        def reject(message):
            return {"accepted": False, "message": message}

        kind = action.kind
        if source == "replay" and state.mode != "replay":
            return reject("回放输入不能写入真实实验")
        if state.mode == "replay":
            source = "replay"
        if kind == "pause":
            if state.status == "completed":
                return reject("实验已完成")
            state.status, state.pause_reason = "paused", action.note or "用户暂停"
            return {"accepted": True, "message": "已暂停；软件不会控制物理电源，请自行确认现场状态。"}
        if kind == "resume":
            if state.status != "paused":
                return reject("当前不处于暂停状态")
            if not action.confirmed:
                return reject("需由现场人员确认可以恢复；Agent 无权替代确认")
            state.status, state.pause_reason = "running", ""
            return {"accepted": True, "message": "已恢复流程，过期的证据仍需重新确认。"}
        if kind == "remeasure":
            if state.status == "paused":
                return reject("实验已暂停，请先由现场人员确认恢复，不能通过重测绕过暂停")
            if state.step < 6:
                return reject("尚未进入测量阶段，不能跳过准备步骤")
            for m in state.measurements:
                m.superseded = True
            state.step, state.status, state.pause_reason = 6, "running", ""
            return {"accepted": True, "message": "已返回测量阶段，历史数据保留，新测量仍需满足检查条件。"}
        if kind == "invalidate_wiring":
            for name in ("power", "connected", "wiring"):
                state.facts.pop(name, None)
            for m in state.measurements:
                m.superseded = True
            state.step, state.status = (1 if "sop" in state.facts else 0), "paused"
            state.pause_reason = "接线发生变化：请先人工断电，确认恢复后重新检查"
            return {"accepted": True, "message": state.pause_reason}
        if state.status != "running":
            return reject("实验已暂停或完成，不能执行此操作")
        if kind in {"advance", "report"}:
            if kind == "report" and state.step != 8:
                return reject("尚未到报告阶段")
            blockers = self.blockers(state)
            if blockers:
                return reject("；".join(blockers))
            if state.step == 8:
                state.status = "completed"
            else:
                state.step += 1
            return {"accepted": True, "message": "实验已完成，可导出报告。" if state.status == "completed"
                    else "已进入：" + self.spec["steps"][state.step]["title"]}
        if not action.confirmed:
            return reject("需要用户明确勾选人工确认，模型不能自行声明检查通过")
        if kind == "read_sop":
            if state.step != 0:
                return reject("请在阅读说明阶段确认")
            state.facts["sop"] = Evidence(value=True, source=source, reference=action.note)
        elif kind == "confirm_equipment":
            if state.step < 2:
                return reject("请先完成阅读说明和断电确认")
            if set(action.objects) - set(self.spec["required_objects"]):
                return reject("器材标签必须来自实验配置")
            state.facts["objects"] = Evidence(value=sorted(set(action.objects)), source=source,
                                              reference=action.note)
        elif kind == "confirm_power_off":
            if state.step == 0 or state.step > 6:
                return reject("请在准备或测量阶段确认断电；报告阶段如需修改请先重测")
            previous = state.facts.get("power")
            if previous and previous.value == "on":
                for name in ("connected", "wiring"):
                    state.facts.pop(name, None)
                for m in state.measurements:
                    m.superseded = True
                state.step = 1
            state.facts["power"] = Evidence(value="off", source=source, reference=action.note)
        elif kind == "confirm_connected":
            if state.step != 3 or not self.fact(state, "power", "off", safety=True):
                return reject("只有连接阶段且有效确认断电后才能确认连接")
            if self.missing_objects(state):
                return reject("请先补齐器材")
            state.facts["connected"] = Evidence(value=True, source=source, reference=action.note)
            state.facts.pop("wiring", None)
        elif kind == "confirm_wiring":
            if state.step != 4 or not self.fact(state, "power", "off", safety=True):
                return reject("只有复核阶段且确认断电后才能复核接线")
            if not self.fact(state, "connected", safety=True):
                return reject("请先确认连接完成")
            state.facts["wiring"] = Evidence(value=True, source=source, reference=action.note)
        elif kind == "confirm_power_on":
            if state.step not in {5, 6}:
                return reject("尚未到允许人工上电的阶段")
            blockers = self.setup_blockers(state, powered=False)
            if blockers:
                return reject("；".join(blockers))
            state.facts["power"] = Evidence(value="on", source=source, reference=action.note)
        elif kind == "manual_measurement":
            if action.value is None:
                return reject("缺少电压数值")
            return self.add_measurement(state, action.value, source, 1, action.note)
        elif kind == "save_measurement":
            if state.step != 7:
                return reject("请在保存阶段核对并保存")
            m = self.active_measurement(state)
            if not m or not self.measurement_ready(state):
                return reject("读数已过期或缺失，请返回重测")
            m.saved = True
        else:
            return reject("不支持的操作")
        return {"accepted": True, "message": "已记录人工确认（不代表硬件自动验证）。"}

    def add_measurement(self, state: State, value: float, source: str,
                        confidence: float, reference: str = "") -> dict:
        if state.status != "running" or state.step != 6:
            return {"accepted": False, "message": "仅测量阶段接收读数"}
        blockers = self.setup_blockers(state)
        if blockers:
            return {"accepted": False, "message": "；".join(blockers)}
        if confidence < self.settings.min_confidence:
            return {"accepted": False, "message": "读数置信度不足，请人工重新读数，不能自动当作有效测量"}
        if not self.spec["voltage_min"] <= value <= self.spec["voltage_max"]:
            return {"accepted": False, "message": "读数超出本实验允许范围，请检查量程、接线和传感器型号"}
        if state.mode == "live" and source == "replay":
            return {"accepted": False, "message": "禁止将回放数据作为真实测量"}
        if state.mode == "replay":
            source = "replay"
        for m in state.measurements:
            if not m.saved:
                m.superseded = True
        state.measurements.append(Measurement(value=value, source=source, at=now(),
                                               confidence=confidence, reference=reference))
        return {"accepted": True, "message": "已接收候选读数，请进入保存阶段核对。"}

    def view(self, state: State) -> dict:
        return {**state.model_dump(), "steps": self.spec["steps"],
                "blockers": self.blockers(state), "missing_objects": self.missing_objects(state),
                "warning": self.spec["warning"]}
