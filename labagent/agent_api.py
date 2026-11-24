"""Explicit decision/execution endpoints; camera frames arrive only after a decision."""

from fastapi import File, Form, UploadFile
from pydantic import Field

from .models import DomainError, StrictModel
from .workflow import expire_candidates


class DecisionRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=80, pattern="^[a-zA-Z0-9_-]+$")


class SamplingUpdate(StrictModel):
    samples: int = Field(ge=1, le=10, strict=True)
    confirmed: bool = False
    expected_revision: int = Field(ge=0)


def mount_agent_api(app, controller, store, engine):
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
