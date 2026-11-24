"""Workflow transitions need real tool evidence, not fabricated confirm actions."""
import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from labagent.models import Action
from labagent.step_checks import RULES, rule_for


def act(client, state, kind, **kwargs):
    response = client.post(f"/api/sessions/{state['id']}/actions", json={"kind": kind, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def meter_stage(client):
    state = client.post("/api/sessions", json={"plan_id": "battery-voltage-15v"}).json()
    state = act(client, state, "confirm_step", confirmed=True)["session"]
    state = act(client, state, "advance")["session"]
    state = act(client, state, "confirm_equipment", confirmed=True, objects=state["plan"]["required_objects"])["session"]
    return act(client, state, "advance")["session"]


def image_file(color=0):
    image = Image.new("RGB", (100, 80), (color, 120, 180))
    buffer = io.BytesIO(); image.save(buffer, format="PNG")
    return {"file": ("scene.png", buffer.getvalue(), "image/png")}


def observe(client, state, task="step", color=0):
    return client.post(f"/api/sessions/{state['id']}/observe", files=image_file(color), data={"task": task, "automatic": "true"})


def passed_meter():
    return {"checks": {"black_in_com": True, "red_in_voltage": True, "dc_voltage_mode": True},
            "meter_range": "dc_20v", "raw_text": "actual tool output", "backend": "test-vision"}


@pytest.mark.parametrize("invalid,expected", [
    ({"checks": {"black_in_com": True}}, "看不清"),
    ({"checks": {"black_in_com": "true", "red_in_voltage": True, "dc_voltage_mode": True}, "meter_range": "dc_20v"}, "看不清"),
    ({"checks": {"black_in_com": True, "red_in_voltage": False, "dc_voltage_mode": True}, "meter_range": "dc_20v"}, "尚未满足"),
    ({"checks": {"black_in_com": True, "red_in_voltage": True, "dc_voltage_mode": True}, "meter_range": "dc_200mv"}, "量程"),
    ({"checks": {"black_in_com": True, "red_in_voltage": True, "dc_voltage_mode": True}, "meter_range": ["dc_20v"]}, "看不清"),
])
def test_uncertain_or_wrong_setup_keeps_observing(client, monkeypatch, invalid, expected):
    state = meter_stage(client)
    monkeypatch.setattr(client.app.state.agent.gateway, "request", lambda *a, **kw: invalid)
    response = observe(client, state)
    assert response.status_code == 200, response.text
    body = response.json(); state = body["session"]
    assert not body["passed"] and expected in body["message"]
    assert not state["checks"].get(state["steps"][state["step"]]["id"])
    assert state["agent_decision"]["tool"] == "inspect_step"
    assert not act(client, state, "advance")["accepted"]


def test_visual_setup_then_signed_reading_without_contact_vlm(client, monkeypatch):
    state = meter_stage(client)
    gateway = client.app.state.agent.gateway
    calls = []
    def request(*a, **kwargs):
        calls.append(kwargs["data"]["rule"])
        if calls[-1] == "meter_setup":
            return passed_meter()
        return {"checks": {"red_on_positive": True, "black_on_negative": True, "no_short": True}}
    monkeypatch.setattr(gateway, "request", request)
    state = observe(client, state).json()["session"]
    assert state["checks"][state["steps"][2]["id"]]["source"] == "vision"
    assert state["facts"]["meter_range"]["value"] == "dc_20v"
    state = act(client, state, "advance", automatic=True)["session"]
    assert state["agent_decision"]["tool"] == "read_display"
    assert state["agent_decision"]["visual_rule"] is None
    assert calls == ["meter_setup"] and "contact" not in state["checks"]


def measurement_stage(client):
    state = meter_stage(client)
    state = act(client, state, "confirm_step", confirmed=True, meter_range="dc_20v")["session"]
    return act(client, state, "advance")["session"]


@pytest.mark.parametrize("second_color,values,accepted", [
    (10, ["1.34", "1.35"], True), (0, ["1.34", "1.34"], False),
    (10, ["1.34", "1.50"], False), (10, [None, "1.34"], False),
])
def test_only_independent_stable_readings_auto_record(client, monkeypatch, second_color, values, accepted):
    import json
    import labagent.demo_api as api
    state = measurement_stage(client)
    replies = iter(values)
    def request(base, key, path, **kwargs):
        return {"roi": [.1, .1, .8, .8]} if path == "locate_display" else {"text": json.dumps({"reading": next(replies), "unit": None})}
    monkeypatch.setattr(client.app.state.agent.gateway, "request", request)
    base = datetime.now(timezone.utc)
    times = iter([(base-timedelta(seconds=3)).isoformat(), base.isoformat()])
    monkeypatch.setattr(api, "now", lambda: next(times))
    first = observe(client, state, "reading")
    assert first.status_code == 200, first.text
    assert not first.json()["session"]["measurements"]
    second = observe(client, first.json()["session"], "reading", second_color)
    assert second.status_code == 200, second.text
    state = second.json()["session"]
    assert bool(state["measurements"]) is accepted
    if accepted:
        measurement = state["measurements"][0]
        assert measurement["source"] == "vision_workflow" and measurement["value"] == 1.35
        assert len(measurement["supporting_candidate_ids"]) == 2
        assert state["candidates"][-1]["confirmation_method"] == "workflow_stability"


def test_auto_save_then_notice_without_visual_cleanup_then_report(client, monkeypatch):
    state = measurement_stage(client)
    state = act(client, state, "manual_measurement", value=1.4, confirmed=True)["session"]
    assert client.post(f"/api/sessions/{state['id']}/actions", json={"kind": "save_measurement", "automatic": True}).status_code == 409
    state = act(client, state, "advance", automatic=True)["session"]
    state = act(client, state, "save_measurement", automatic=True)["session"]
    assert state["measurements"][0]["saved"]
    assert state["interaction"]["phase"] == "notice"
    monkeypatch.setattr(client.app.state.agent.gateway, "request", lambda *a, **kw: pytest.fail("No cleanup VLM"))
    assert not act(client, state, "advance", automatic=True)["accepted"]
    client.app.state.store.mutate(state["id"], "test.notice_elapsed", {}, lambda s:setattr(s,"completion_notice_until",(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()))
    state = act(client, state, "advance", automatic=True)["session"]
    state = act(client, state, "report", automatic=True)["session"]
    assert state["status"] == "completed"
    report = client.get(f"/api/sessions/{state['id']}/export?format=md").text
    assert "1.4" in report


def test_step_result_cannot_overwrite_a_concurrent_pause(client, monkeypatch):
    state = meter_stage(client)
    def request(*a, **kw):
        act(client, state, "pause")
        return passed_meter()
    monkeypatch.setattr(client.app.state.agent.gateway, "request", request)
    response = observe(client, state)
    assert response.status_code == 409
    state = client.get(f"/api/sessions/{state['id']}").json()
    assert state["status"] == "paused" and not state["checks"].get(state["steps"][2]["id"])


def test_unmapped_document_step_does_not_claim_visual_support(client):
    from labagent.models import State
    from labagent.plans import compile_document
    plan = compile_document("观察纸张颜色\n实验器材：纸张、笔\n实验步骤\n1. 观察纸张颜色。\n2. 写下颜色并整理。", "paper.txt")
    state = State(title=plan.title, plan=plan.model_dump(), step=2)
    assert rule_for(state) is None


def test_qwen3_step_endpoint_parses_unknowns_without_faking_results(monkeypatch):
    from fastapi import UploadFile
    from scripts import qwen3vl_gguf_service as service
    monkeypatch.setattr(service, "infer", lambda *a, **kw: '```json\n{"checks":{"meter_off":null,"probes_removed":true}}\n```')
    buffer = io.BytesIO(); Image.new("RGB", (100, 80)).save(buffer, format="PNG"); buffer.seek(0)
    result = service.check_step(UploadFile(file=buffer, filename="scene.png"), "battery_cleanup")
    assert result["checks"] == {"meter_off": None, "probes_removed": True}
    assert result["backend"].startswith("qwen3-vl")
