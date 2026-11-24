"""Polarity gates use signed fresh readings, without attesting physical contact."""

import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from labagent.battery_polarity import reading_issue
from labagent.config import ROOT
from labagent.models import Action, Evidence, NewSession, State
from labagent.plans import Plan, compile_document
from labagent.step_checks import reading_progress, rule_for


def template():
    plan = json.loads((ROOT / "configs/battery_plan.json").read_text(encoding="utf-8"))
    plan["measurement"].pop("polarity_check", None)
    plan["steps"].insert(
        3,
        {
            "id": "contact",
            "title": "红正黑负，保持接触稳定",
            "instruction": "红表笔接电池正极，黑表笔接负极，避免短接。",
            "gate": "manual_check",
        },
    )
    plan["steps"].insert(
        -1,
        {
            "id": "cleanup",
            "title": "移开表笔并关闭万用表",
            "instruction": "移开两根表笔，关闭万用表。",
            "gate": "manual_check",
        },
    )
    return plan


def prepare(client, legacy=False, signed=False):
    plan = template() if legacy else Plan.model_validate(template()).model_dump()
    if signed:
        plan["measurement"]["polarity_check"] = "signed"
    state = client.app.state.store.create(NewSession(title="POLARITY TEST"), plan)

    def ready(s):
        s.step = next(i for i, x in enumerate(s.plan["steps"]) if x["gate"] == "measurement")
        s.facts["objects"] = Evidence(value=plan["required_objects"], source="manual")
        s.facts["meter_range"] = Evidence(value="dc_20v", source="manual")
        for x in plan["steps"][: s.step]:
            if x["gate"] in {"read_sop", "meter_setup"}:
                s.checks[x["id"]] = Evidence(value=True, source="manual")
        return {"accepted": True}

    client.app.state.store.mutate(state.id, "test.prepare", {}, ready)
    return state.id


def camera(client, monkeypatch, values):
    values = iter(values)

    def request(base, key, path, **kwargs):
        assert path in {"locate_display", "recognize"}, "Contact VLM must not run"
        return (
            {"roi": [0, 0, 1, 1]}
            if path == "locate_display"
            else {
                "text": json.dumps({"reading": next(values), "unit": "V"}),
                "backend": "explicit-test-fixture",
            }
        )

    monkeypatch.setattr(client.app.state.agent.gateway, "request", request)
    import labagent.demo_api as api

    times = iter((datetime.now(timezone.utc) - timedelta(seconds=30 - i * 2)).isoformat() for i in range(12))
    monkeypatch.setattr(api, "now", lambda: next(times))

    def observe(sid, n, capture_id="", expected_status=200):
        buffer = io.BytesIO()
        Image.new("RGB", (100, 80), (n, 120, 180)).save(buffer, "PNG")
        response = client.post(
            f"/api/sessions/{sid}/observe",
            files={"file": ("lcd.png", buffer.getvalue(), "image/png")},
            data={"task": "reading", "automatic": "true", "capture_id": capture_id},
        )
        assert response.status_code == expected_status, response.text
        return response.json()

    return observe


def test_new_plan_merges_contact_instructions_without_visual_attestation():
    plan = Plan.model_validate(template())
    assert len(plan.steps) == 6 and all(s.id not in {"contact", "cleanup"} for s in plan.steps)
    assert plan.measurement.polarity_check == "positive"
    measure = next(s for s in plan.steps if s.gate == "measurement")
    assert "红表笔接电池正极" in measure.instruction and "负数" in measure.instruction
    assert Plan.model_validate(plan.model_dump()).model_dump() == plan.model_dump()


def test_numbered_document_merges_contact_and_keeps_source_text():
    source = "1.5 V电池电压实验\n实验器材：电池、万用表\n实验步骤\n1. 黑表笔COM，直流电压档。\n2. 红表笔接正极、黑表笔接负极。\n3. 读取电压。"
    plan = compile_document(source, "battery.txt")
    assert plan.source_text == source
    assert not any(s.gate == "manual_check" for s in plan.steps)
    assert "红表笔接正极" in next(s for s in plan.steps if s.gate == "measurement").instruction


def test_legacy_session_skips_contact_without_changing_history_or_faking_checks(engine):
    plan = template()
    state = State(title="legacy", plan=plan, step=2)
    state.checks["meter"] = Evidence(value=True, source="manual")
    assert engine.apply(state, Action(kind="advance"))["accepted"]
    assert state.step == 4 and state.plan == plan and "contact" not in state.checks
    assert not rule_for(state)
    state.step = 3
    assert engine.blockers(state) == [] and rule_for(state) is None
    assert engine.apply(state, Action(kind="advance"))["accepted"] and state.step == 4


@pytest.mark.parametrize("value", ["-1.34", "-0.00", "0.00"])
def test_nonpositive_readings_are_retained_but_not_counted(client, monkeypatch, value):
    sid = prepare(client, legacy=True)
    observe = camera(client, monkeypatch, [value, value])
    first, second = observe(sid, 1), observe(sid, 2)
    for result in (first, second):
        assert result["candidate"]["raw_text"] and result["candidate"]["status"] == "rejected"
        assert not result["session"]["measurements"] and not result.get("auto_recorded")
        assert result["session"]["interaction"]["phase"] == "waiting_scene"
        assert "本次不计入测量" in result["message"]
    state = client.app.state.store.get(sid)
    assert reading_progress(client.app.state.engine, state)["count"] == 0
    assert client.app.state.task_agent.available(state) == {"read_display": {}}
    assert "contact" not in state.checks
    report = client.get(f"/api/sessions/{sid}/export?format=md").text
    assert "极性与有效电压检查" in report and "人工拒绝" not in report


def test_after_negative_two_fresh_positive_frames_are_required_then_save(client, monkeypatch):
    sid = prepare(client)
    observe = camera(client, monkeypatch, ["1.34", "-1.34", "1.34", "1.35"])
    assert not observe(sid, 1).get("auto_recorded")
    assert not observe(sid, 2).get("auto_recorded")
    assert not observe(sid, 3).get("auto_recorded")
    final = observe(sid, 4)
    assert final["auto_recorded"] and final["session"]["measurements"][0]["value"] == 1.35
    assert not final["session"]["risk"] and len(final["session"]["candidates"]) == 4
    assert "no_short" not in final["session"]["facts"]
    for kind in ("advance", "save_measurement"):
        response = client.post(f"/api/sessions/{sid}/actions", json={"kind": kind, "automatic": True}).json()
        assert response["accepted"]
    assert response["session"]["measurements"][0]["saved"]


@pytest.mark.parametrize("value", [-1.3, -0.0, 0.0])
def test_manual_entry_cannot_bypass_positive_policy(client, value):
    sid = prepare(client)
    result = client.post(
        f"/api/sessions/{sid}/actions", json={"kind": "manual_measurement", "value": value, "confirmed": True}
    ).json()
    assert not result["accepted"] and not result["session"]["measurements"]


def test_signed_experiment_and_nonbattery_voltage_still_accept_negatives(client):
    sid = prepare(client, signed=True)
    result = client.post(
        f"/api/sessions/{sid}/actions", json={"kind": "manual_measurement", "value": -1.3, "confirmed": True}
    ).json()
    assert result["accepted"] and result["session"]["measurements"][0]["value"] == -1.3
    assert not reading_issue(
        {"measurement": {"adapter": "voltage"}, "object_labels": {"sensor": "传感器"}}, -1.3
    )


def test_explicit_manual_contact_and_signed_plans_are_not_silently_merged():
    raw = template()
    raw["steps"][3]["visual_check"] = "manual"
    assert any(s.id == "contact" for s in Plan.model_validate(raw).steps)
    raw = template()
    raw["measurement"]["polarity_check"] = "signed"
    assert any(s.id == "contact" for s in Plan.model_validate(raw).steps)


def test_negative_candidate_requires_new_capture_instead_of_changing_its_sign(client, monkeypatch):
    sid = prepare(client)
    negative = camera(client, monkeypatch, ["-1.34"])(sid, 1)
    result = client.post(
        f"/api/sessions/{sid}/actions",
        json={
            "kind": "confirm_candidate",
            "candidate_id": negative["candidate_id"],
            "value": 1.34,
            "confirmed": True,
        },
    ).json()
    assert not result["accepted"] and not result["session"]["measurements"]


def test_out_of_plan_positive_cannot_form_a_stable_pair(client, monkeypatch):
    sid = prepare(client)
    observe = camera(client, monkeypatch, ["2.01", "1.99", "1.99"])
    assert not observe(sid, 1).get("auto_recorded")
    assert not observe(sid, 2).get("auto_recorded")
    assert observe(sid, 3)["auto_recorded"]
