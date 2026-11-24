from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


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
    unit: Literal["V"] = "V"
    source: Literal["manual", "ocr", "serial", "replay"]
    at: str
    confidence: float = Field(ge=0, le=1)
    reference: str = ""
    saved: bool = False
    superseded: bool = False


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


class NewSession(StrictModel):
    title: str = Field(default="低压模拟传感器电压实验", min_length=1, max_length=120)
    mode: Literal["live", "replay"] = "live"


class Action(StrictModel):
    kind: Literal[
        "read_sop", "confirm_power_off", "confirm_equipment", "confirm_connected",
        "confirm_wiring", "confirm_power_on", "manual_measurement", "save_measurement",
        "advance", "remeasure", "pause", "resume", "invalidate_wiring", "report"
    ]
    value: float | None = None
    objects: list[str] = Field(default_factory=list, max_length=30)
    note: str = Field(default="", max_length=500)
    confirmed: bool = False
    expected_revision: int | None = Field(default=None, ge=0)


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
