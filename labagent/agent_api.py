"""Explicit decision/execution endpoints; camera frames arrive only after a decision."""

import re
import time

from fastapi import File, Form, UploadFile
from pydantic import Field

from .models import DomainError, StrictModel
from .perception import load_image
from .providers import ProviderError
from .workflow import expire_candidates


class DecisionRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=80, pattern="^[a-zA-Z0-9_-]+$")


class SamplingUpdate(StrictModel):
    samples: int = Field(ge=1, le=10, strict=True)
    confirmed: bool = False
    expected_revision: int = Field(ge=0)


def mount_agent_api(app, controller, store, engine):
    @app.post("/api/sessions/{session_id}/agent/read-frame")
    def read_frame(session_id: str, decision_id: str = Form(...),
                   file: UploadFile = File(...), capture_id: str = Form(...)):
        try:
            if not re.fullmatch(r"[0-9a-f]{32}", capture_id):
                raise DomainError("摄像头采集编号无效", 422)
            state = store.get(session_id)
            with store.connect() as db:
                ticket = db.execute("SELECT * FROM agent_runs WHERE id=? AND session_id=?",
                                    (decision_id, session_id)).fetchone()
            if not ticket or ticket["tool"] != "read_display":
                raise DomainError("请等待读表工具就绪", 409)
            if ticket["status"] == "completed":
                result = controller.execute(session_id, decision_id, app.state.perform_observation, file)
                return {**result, "session": engine.view(store.get(session_id))}
            if (ticket["status"] != "proposed" or state.status != "running"
                    or state.revision != ticket["revision"] or "read_display" not in controller.available(state)):
                raise DomainError("实验状态已变化，请按当前步骤继续", 409)
            raw = file.file.read(8_000_001)
            if len(raw) > 8_000_000:
                raise DomainError("图片上限8 MB", 413)
            load_image(raw)
            settings = app.state.agent.settings
            try:
                located = app.state.agent.gateway.request(
                    settings.ocr_base_url, "", "locate_display", timeout=8,
                    data={"fast_only": "true"}, files={"file": ("scene.jpg", raw, "image/jpeg")})
            except ProviderError:
                located = {"roi": None, "meter_present": None, "location_error": "定位服务暂时未返回结果"}
            current = store.get(session_id)
            if current.revision != state.revision or current.status != "running":
                raise DomainError("定位期间实验状态已变化", 409)
            roi = located.get("roi")
            if not roi:
                return {"located": False, "meter_present": located.get("meter_present"),
                        "location_error": located.get("location_error"),
                        "session": engine.view(current)}
            file.file.seek(0)

            def observe_single(sid, image, task, ignored_roi, automatic, **options):
                result = app.state.perform_observation(
                    sid, image, task, ",".join(str(v) for v in roi), automatic, **options,
                    capture_id=capture_id, burst_start=True, reading_timeout=8, single_reading=True)
                if not result.get("auto_recorded"):
                    candidate = result.get("candidate", {})
                    reason = result.get("message") or candidate.get("reason") or "显示屏数字未读清。"
                    result = {**result, "message": reason + "本轮未记录，可以移开表笔，调整后再开始。"}
                return {**result, "located": True, "single_frame": True}

            result = controller.execute(session_id, decision_id, observe_single, file)
            return {**result, "session": engine.view(store.get(session_id))}
        finally:
            file.file.close()

    @app.post("/api/sessions/{session_id}/agent/replan")
    def replan(session_id: str, body: SamplingUpdate):
        if not body.confirmed:
            raise DomainError("请核对采样目标后明确确认调整", 422)

        def apply(state):
            if (
                state.automation != "agent"
                or not state.plan
                or not state.plan.get("measurement")
                or state.status == "paused"
            ):
                raise DomainError("请在可运行的Agent测量实验中调整采样目标", 409)
            count = sum(not m.superseded for m in state.measurements)
            if body.samples < count:
                raise DomainError("采样目标不能小于已有有效记录数；历史读数不会被自动删除", 422)
            old = state.plan["measurement"]["samples"]
            state.plan["measurement"]["samples"] = body.samples
            state.plan.setdefault("review_notes", []).append(
                f"用户审核采样目标调整：{old} → {body.samples}。"
            )
            index = next(i for i, item in enumerate(state.plan["steps"]) if item["gate"] == "measurement")
            if state.step > index and count < body.samples:
                state.step = index
                state.status = "running"
                for item in state.plan["steps"][index + 1 :]:
                    state.checks.pop(item["id"], None)
                    state.facts.pop("workflow_check:" + item["id"], None)
                expire_candidates(state)
            return {"accepted": True, "message": f"采样目标已调整为 {body.samples} 次，已有记录保留。"}

        state, result = store.mutate(
            session_id, "agent.plan_updated", body.model_dump(), apply, body.expected_revision
        )
        return {**result, "session": engine.view(state)}

    @app.post("/api/sessions/{session_id}/agent/decide")
    def decide(session_id: str, body: DecisionRequest):
        decision = controller.decide(session_id, body.request_id, body.expected_revision)
        return {"decision": decision, "session": engine.view(store.get(session_id))}

    @app.post("/api/sessions/{session_id}/agent/execute")
    def execute(session_id: str, decision_id: str = Form(...), file: UploadFile | None = File(default=None), capture_id: str = Form(default="")):
        try:
            state = store.get(session_id)
            if state.mode == "live":
                with store.connect() as db:
                    row = db.execute("SELECT tool FROM agent_runs WHERE id=? AND session_id=?",
                                     (decision_id, session_id)).fetchone()
                if row and row["tool"] == "read_display":
                    raise DomainError("请刷新页面，使用“已接好，开始读数”启动本轮采集。", 409)
            result = controller.execute(session_id, decision_id, app.state.perform_observation, file, capture_id=capture_id)
            return {**result, "decision_id": decision_id, "session": engine.view(store.get(session_id))}
        finally:
            if file:
                file.file.close()

    @app.get("/api/sessions/{session_id}/agent/context")
    def context(session_id: str):
        state = store.get(session_id)
        return {
            "automation": state.automation,
            "decision": controller.summary(state) if state.plan else None,
            "latest_preview": controller.preview(session_id),
        }

    @app.post("/api/sessions/{session_id}/agent/read-burst")
    def read_burst(session_id: str, decision_id: str = Form(...),
                   first: UploadFile = File(...), second: UploadFile = File(...),
                   first_id: str = Form(...), second_id: str = Form(...)):
        try:
            if (not all(re.fullmatch(r"[0-9a-f]{32}", key) for key in (first_id, second_id))
                    or first_id == second_id):
                raise DomainError("需要两次不同的摄像头采集", 422)
            with store.connect() as db:
                ticket = db.execute("SELECT tool FROM agent_runs WHERE id=? AND session_id=?",
                                    (decision_id, session_id)).fetchone()
            if not ticket or ticket["tool"] != "read_display":
                raise DomainError("请先等待读表工具准备完成", 409)

            def observe_pair(sid, file, task, roi, automatic, **options):
                started = time.monotonic()
                result = app.state.perform_observation(
                    sid, file, task, roi, automatic, **options,
                    capture_id=first_id, burst_start=True, reading_timeout=4)
                candidate = result.get("candidate", {})
                readings = [dict(candidate)]
                if (candidate.get("value") is not None and candidate.get("status") == "pending"
                        and result["session"]["status"] == "running"):
                    time.sleep(max(0, 1.05 - (time.monotonic() - started)))
                    options["expected_revision"] = result["session"]["revision"]
                    result = app.state.perform_observation(
                        sid, second, task, roi, automatic, **options,
                        capture_id=second_id, reading_timeout=4)
                    readings.append(dict(result.get("candidate", {})))
                message = "已取得并记录本次读数，可以移开表笔。"
                if not result.get("auto_recorded"):
                    values = [
                        f"第{i+1}帧" + (f"{c['value']:g} {c.get('unit', 'V')}" if c.get("value") is not None else "未读清")
                        for i, c in enumerate(readings)
                    ]
                    last = readings[-1]
                    if last.get("value") is None or last.get("status") == "rejected":
                        reason = last.get("reason", "本次读数无效。")
                    else:
                        spec = result["session"]["plan"]["measurement"]
                        if not spec["min_value"] <= last["value"] <= spec["max_value"]:
                            reason = f"读数超出计划接收范围{spec['min_value']:g}至{spec['max_value']:g} V。"
                        else:
                            reason = "两帧未通过稳定性核验，读数需相差不超过0.02 V。"
                    message = "，".join(values) + "。" + reason + "本轮未记录，可以移开表笔，调整后再开始。"
                return {**result, "message": message, "reading_burst": True}

            result = controller.execute(session_id, decision_id, observe_pair, first)
            return {**result, "session": engine.view(store.get(session_id))}
        finally:
            first.file.close()
            second.file.close()
