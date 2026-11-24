"""Static video may yield identical pixels; a reminder never attests physical cleanup."""

from datetime import datetime, timedelta, timezone

from labagent.models import Action, State
from labagent.step_checks import reading_progress

from .test_battery_polarity import camera, prepare, template


def test_identical_camera_frames_with_identical_values_are_valid(client, monkeypatch):
    sid = prepare(client)
    observe = camera(client, monkeypatch, ["1.34", "1.34"])
    assert not observe(sid, 1, "a" * 32).get("auto_recorded")
    result = observe(sid, 1, "b" * 32)
    assert result["auto_recorded"]
    state = client.app.state.store.get(sid)
    first, second = state.candidates
    assert first.reference == second.reference and first.value == second.value == 1.34
    assert first.id != second.id and len(state.measurements[0].supporting_candidate_ids) == 2
    report = client.get(f"/api/sessions/{sid}/export?format=md").text
    assert "两帧稳定性核验" in report


def test_reusing_capture_id_is_not_a_second_observation(client, monkeypatch):
    sid = prepare(client)
    observe = camera(client, monkeypatch, ["1.34", "1.34"])
    observe(sid, 1, "a" * 32)
    result = observe(sid, 1, "a" * 32, expected_status=409)
    assert "已处理" in result["detail"]
    state = client.app.state.store.get(sid)
    assert len(state.candidates) == 1 and not state.measurements


def test_camera_capture_ids_still_require_one_second_and_matching_range(client, monkeypatch):
    import labagent.demo_api as api

    sid = prepare(client)
    observe = camera(client, monkeypatch, ["1.34", "1.34"])
    base = datetime.now(timezone.utc) - timedelta(seconds=10)
    times = iter([base.isoformat(), (base + timedelta(milliseconds=500)).isoformat()])
    monkeypatch.setattr(api, "now", lambda: next(times))
    observe(sid, 1, "a" * 32)
    assert not observe(sid, 1, "b" * 32).get("auto_recorded")
    state = client.app.state.store.get(sid)
    assert not reading_progress(client.app.state.engine, state)["stable"]
    state.candidates[-1].at = (base + timedelta(seconds=2)).isoformat()
    state.candidates[-1].meter_range = "dc_2v"
    assert not reading_progress(client.app.state.engine, state)["stable"]


def test_cleanup_notice_survives_refresh_and_duplicate_save_without_reset(client):
    sid = prepare(client)
    for action in (
        {"kind": "manual_measurement", "value": 1.34, "confirmed": True},
        {"kind": "advance"},
        {"kind": "save_measurement", "automatic": True},
    ):
        result = client.post(f"/api/sessions/{sid}/actions", json=action).json()
        assert result["accepted"]
    until = result["session"]["completion_notice_until"]
    refreshed = client.get(f"/api/sessions/{sid}").json()
    assert (
        refreshed["interaction"]["phase"] == "notice" and refreshed["interaction"]["primary_action"] is None
    )
    assert "关闭万用表" in refreshed["interaction"]["message"]
    duplicate = client.post(
        f"/api/sessions/{sid}/actions", json={"kind": "save_measurement", "confirmed": True}
    ).json()
    assert duplicate["session"]["completion_notice_until"] == until
    assert not client.post(f"/api/sessions/{sid}/actions", json={"kind": "advance"}).json()["accepted"]
    state = client.app.state.store.get(sid)
    assert client.app.state.task_agent.available(state) == {}
    client.app.state.store.mutate(
        sid,
        "test.notice_elapsed",
        {},
        lambda s: setattr(
            s, "completion_notice_until", (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        ),
    )
    advanced = client.post(f"/api/sessions/{sid}/actions", json={"kind": "advance"}).json()
    assert (
        advanced["accepted"] and advanced["session"]["steps"][advanced["session"]["step"]]["gate"] == "report"
    )
    assert "cleanup" not in advanced["session"]["checks"]


def test_old_cleanup_step_is_omitted_without_changing_plan_or_faking_confirmation(engine):
    plan = template()
    state = State(title="legacy", plan=plan, step=5)
    state.step = next(i for i, s in enumerate(plan["steps"]) if s["gate"] == "save")
    from labagent.models import Measurement, now

    state.measurements.append(Measurement(value=1.34, source="manual", confidence=1, at=now(), saved=True))
    result = engine.apply(state, Action(kind="advance"))
    assert result["accepted"] and result["omitted_cleanup_steps"] == ["cleanup"]
    assert state.plan == plan and "cleanup" not in state.checks


def test_empty_notice_does_not_rewrite_historical_state_schema():
    state = State(title="legacy")
    assert "completion_notice_until" not in state.model_dump()


def test_resuming_an_old_cleanup_stage_shows_notice_without_camera(engine):
    from labagent.models import Measurement, now

    plan = template()
    state = State(title="legacy cleanup", plan=plan, status="paused")
    state.step = next(i for i, s in enumerate(plan["steps"]) if s["id"] == "cleanup")
    state.measurements.append(Measurement(value=1.34, source="manual", confidence=1, at=now(), saved=True))
    assert engine.apply(state, Action(kind="resume", confirmed=True))["accepted"]
    view = engine.view(state)
    assert view["interaction"]["phase"] == "notice" and view["agent_decision"]["visual_rule"] is None
    assert "cleanup" not in state.checks
