from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_serializer


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return uuid4().hex


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Evidence(StrictModel):
    value: Any
    source: Literal["manual", "vision", "ocr", "serial", "replay"]
    at: str = Field(default_factory=now)
    confidence: float = Field(default=1, ge=0, le=1)
    reference: str = ""


class Measurement(StrictModel):
    id: str = Field(default_factory=uid)
    value: float
    unit: str = Field(default="V", min_length=1, max_length=16)
    source: Literal["manual", "ocr", "serial", "replay", "vision_confirmed", "vision_workflow"]
    at: str
    confidence: float = Field(ge=0, le=1)
    reference: str = ""
    saved: bool = False
    superseded: bool = False
    candidate_id: str = ""
    original_value: float | None = None
    supporting_candidate_ids: list[str] = Field(default_factory=list)


class ReadingCandidate(StrictModel):
    id: str = Field(default_factory=uid)
    value: float | None = None
    unit: str = "V"
    at: str = Field(default_factory=now)
    backend: str = ""
    raw_text: str = ""
    reference: str = ""
    roi: list[float] = Field(default_factory=list)
    meter_range: str = ""
    reason: str = ""
    status: Literal["pending", "confirmed", "rejected", "expired"] = "pending"
    confirmed_value: float | None = None
    step_id: str = ""
    confirmation_method: Literal["", "manual", "workflow_stability"] = ""


class State(StrictModel):
    id: str = Field(default_factory=uid)
    title: str
    mode: Literal["live", "replay"] = "live"
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    revision: int = 0
    step: int = 0
    status: Literal["running", "paused", "completed"] = "running"
    pause_reason: str = ""
    facts: dict[str, Evidence] = Field(default_factory=dict)
    measurements: list[Measurement] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    checks: dict[str, Evidence] = Field(default_factory=dict)
    candidates: list[ReadingCandidate] = Field(default_factory=list)
    risk: str = ""
    automation: Literal["workflow", "agent"] = "workflow"
    completion_notice_until: str = ""

    @model_serializer(mode="wrap")
    def serialize_state(self, handler):
        result = handler(self)
        if not self.completion_notice_until:
            result.pop("completion_notice_until", None)
        return result


class NewSession(StrictModel):
    title: str = Field(default="低压模拟传感器电压实验", min_length=1, max_length=120)
    mode: Literal["live", "replay"] = "live"
    plan_id: str | None = None
    automation: Literal["workflow", "agent"] = "workflow"


class Action(StrictModel):
    kind: Literal[
        "read_sop", "confirm_power_off", "confirm_equipment", "confirm_connected",
        "confirm_wiring", "confirm_power_on", "manual_measurement", "save_measurement",
        "advance", "remeasure", "pause", "resume", "invalidate_wiring", "report",
        "confirm_step", "confirm_meter_setup", "confirm_candidate", "reject_candidate"
    ]
    value: float | None = None
    objects: list[str] = Field(default_factory=list, max_length=30)
    note: str = Field(default="", max_length=500)
    confirmed: bool = False
    expected_revision: int | None = Field(default=None, ge=0)
    candidate_id: str = ""
    meter_range: str = ""
    automatic: bool = False
    advance_after: bool = False


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=4000)


class ReplayEvent(StrictModel):
    kind: Literal["objects", "power", "connected", "wiring", "measurement", "action"]
    data: dict[str, Any]


class ReplayRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    events: list[ReplayEvent] = Field(min_length=1, max_length=200)


class DomainError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status
