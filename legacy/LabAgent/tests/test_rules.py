from datetime import datetime, timedelta, timezone

import pytest

from labagent.models import Action, Evidence, State

from .conftest import measurement_stage


def state():
    return State(title="test")


def test_full_workflow(engine):
    s = measurement_stage(engine, state())
    for action in [Action(kind="manual_measurement", value=1.25, confirmed=True), Action(kind="advance"),
                   Action(kind="save_measurement", confirmed=True), Action(kind="advance"), Action(kind="report")]:
        assert engine.apply(s, action)["accepted"]
    assert s.status == "completed" and s.measurements[0].saved


@pytest.mark.parametrize("kind", ["read_sop", "confirm_power_off", "confirm_equipment", "confirm_wiring"])
def test_manual_confirmation_required(engine, kind):
    assert not engine.apply(state(), Action(kind=kind))["accepted"]


def test_missing_power_blocks(engine):
    s = state()
    engine.apply(s, Action(kind="read_sop", confirmed=True))
    engine.apply(s, Action(kind="advance"))
    assert not engine.apply(s, Action(kind="advance"))["accepted"]
    assert s.step == 1


def test_missing_meter_blocks(engine):
    s = state()
    for a in [Action(kind="read_sop", confirmed=True), Action(kind="advance"),
              Action(kind="confirm_power_off", confirmed=True), Action(kind="advance"),
              Action(kind="confirm_equipment", objects=["sensor", "breadboard", "wires"], confirmed=True)]:
        assert engine.apply(s, a)["accepted"]
    assert engine.missing_objects(s) == ["multimeter"]
    assert not engine.apply(s, Action(kind="advance"))["accepted"]


def test_visual_power_is_not_safety_evidence(engine):
    s = state()
    s.step = 1
    s.facts["power"] = Evidence(value="off", source="vision")
    assert engine.blockers(s)


@pytest.mark.parametrize("value", [-0.01, 3.31, 999])
def test_out_of_range_rejected(engine, value):
    s = measurement_stage(engine, state())
    assert not engine.add_measurement(s, value, "ocr", 1)["accepted"]
    assert not s.measurements


def test_zero_is_a_valid_measurement(engine):
    s = measurement_stage(engine, state())
    assert engine.add_measurement(s, 0.0, "manual", 1)["accepted"]


def test_low_confidence_not_recorded(engine):
    s = measurement_stage(engine, state())
    assert not engine.add_measurement(s, 1.25, "ocr", .2)["accepted"]
    assert not s.measurements


def test_stale_power_blocks_measurement(engine):
    s = measurement_stage(engine, state())
    s.facts["power"].at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert not engine.add_measurement(s, 1.25, "manual", 1)["accepted"]


def test_remeasure_retains_old_results(engine):
    s = measurement_stage(engine, state())
    engine.apply(s, Action(kind="manual_measurement", value=1.25, confirmed=True))
    engine.apply(s, Action(kind="advance"))
    engine.apply(s, Action(kind="save_measurement", confirmed=True))
    old = s.measurements[0].model_dump()
    assert engine.apply(s, Action(kind="remeasure"))["accepted"]
    assert s.measurements[0].saved and s.measurements[0].superseded
    assert engine.apply(s, Action(kind="manual_measurement", value=1.4, confirmed=True))["accepted"]
    assert len(s.measurements) == 2 and s.measurements[0].value == old["value"]


def test_no_skip_to_measurement(engine):
    assert not engine.apply(state(), Action(kind="remeasure"))["accepted"]


def test_pause_requires_human_resume(engine):
    s = measurement_stage(engine, state())
    engine.apply(s, Action(kind="pause"))
    assert not engine.apply(s, Action(kind="advance"))["accepted"]
    assert not engine.apply(s, Action(kind="remeasure"))["accepted"]
    assert not engine.apply(s, Action(kind="resume"))["accepted"]
    assert engine.apply(s, Action(kind="resume", confirmed=True))["accepted"]


def test_rewiring_invalidates_safety_and_readings(engine):
    s = measurement_stage(engine, state())
    engine.apply(s, Action(kind="manual_measurement", value=1.25, confirmed=True))
    assert engine.apply(s, Action(kind="invalidate_wiring"))["accepted"]
    assert s.step == 1 and s.status == "paused"
    assert not {"power", "connected", "wiring"} & set(s.facts)
    assert s.measurements[0].superseded


def test_rewire_cannot_skip_sop(engine):
    s = state()
    engine.apply(s, Action(kind="invalidate_wiring"))
    engine.apply(s, Action(kind="resume", confirmed=True))
    assert s.step == 0 and engine.blockers(s)


def test_replay_never_becomes_real_measurement(engine):
    s = state()
    s.mode = "replay"
    measurement_stage(engine, s)
    engine.apply(s, Action(kind="manual_measurement", value=1.25, confirmed=True))
    assert s.measurements[0].source == "replay"


def test_replay_source_blocked_in_live(engine):
    s = measurement_stage(engine, state())
    assert not engine.add_measurement(s, 1.25, "replay", 1)["accepted"]


def test_previous_candidate_superseded(engine):
    s = measurement_stage(engine, state())
    engine.add_measurement(s, 1.1, "ocr", 1)
    engine.add_measurement(s, 1.2, "ocr", 1)
    assert s.measurements[0].superseded and not s.measurements[1].superseded
