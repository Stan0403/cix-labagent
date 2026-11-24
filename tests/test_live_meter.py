"""An old visual result must never advance after the camera or experiment changes."""

import io
import json

import pytest
from PIL import Image, ImageDraw

from labagent.meter_live_model import decode_reply, meter_region
from labagent.models import Evidence, NewSession


def frame(change=None):
    image = Image.new("RGB", (480, 640), (155, 160, 165))
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 60, 410, 620), fill=(210, 150, 35))
    draw.ellipse((110, 205, 370, 465), fill=(25, 30, 35))
    draw.line((240, 330, 300, 250), fill=(230, 230, 230), width=7)
    draw.rectangle((130, 535, 165, 575), fill="black")
    draw.rectangle((300, 535, 335, 575), fill="red")
    if change == "dial":
        draw.line((240, 330, 300, 250), fill=(25, 30, 35), width=9)
        draw.line((240, 330, 190, 265), fill="white", width=7)
    elif change == "plug":
        draw.rectangle((300, 535, 335, 575), fill=(25, 30, 35))
    elif change == "background":
        draw.rectangle((0, 0, 20, 30), fill="blue")
    data = io.BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


@pytest.fixture
def live_meter(client, settings, monkeypatch):
    settings.meter_live_enabled = True
    store = client.app.state.store
    plan = client.app.state.plans.get("battery-voltage-15v").model_dump()
    state = store.create(NewSession(automation="agent"), plan)

    def prepare(s):
        s.step = 2
        s.checks[plan["steps"][0]["id"]] = Evidence(value=True, source="manual")
        s.facts["objects"] = Evidence(value=plan["required_objects"], source="manual")
        return {"accepted": True}

    state = store.mutate(state.id, "test.prepare", {}, prepare)[0]
    response = {
        "checks": {"black_in_com": True, "red_in_voltage": True, "dc_voltage_mode": True},
        "meter_range": "dc_20v",
        "roi": [0.1, 0.05, 0.8, 0.9],
        "backend": "explicit-fixture",
    }

    def infer(*args, **kwargs):
        assert args[2] == "check_meter_live"
        return response

    monkeypatch.setattr(client.app.state.agent.gateway, "request", infer)
    return client, state, response, settings


def preview(env, raw=None):
    client, state, _, _ = env
    return client.post(
        f"/api/sessions/{state.id}/meter-live/preview",
        files={"file": ("frame.png", raw or frame(), "image/png")},
    )


def commit(env, token, raw=None):
    client, state, _, _ = env
    return client.post(
        f"/api/sessions/{state.id}/meter-live/commit",
        data={"preview_id": token},
        files={"file": ("current.png", raw or frame(), "image/png")},
    )


def test_preview_is_read_only_and_only_current_frame_can_commit_and_advance(live_meter):
    client, state, _, settings = live_meter
    before = client.get(f"/api/sessions/{state.id}/events").json()
    observed = preview(live_meter).json()
    assert observed["check"]["passed"] and observed["preview_id"]
    assert client.get(f"/api/sessions/{state.id}").json()["revision"] == state.revision
    assert client.get(f"/api/sessions/{state.id}/events").json() == before
    assert not (settings.data_dir / "images").exists()
    adopted = commit(live_meter, observed["preview_id"]).json()
    assert adopted["accepted"] and adopted["advanced"] and adopted["session"]["step"] == 3
    assert adopted["session"]["checks"]["meter"]["source"] == "vision"
    assert adopted["session"]["facts"]["meter_range"]["value"] == "dc_20v"
    assert adopted["session"]["facts"]["workflow_check:meter"]["value"]["method"] == "live_scene_verified"
    assert commit(live_meter, observed["preview_id"]).status_code == 409


@pytest.mark.parametrize("change", ["dial", "plug"])
def test_dial_or_plug_change_rejects_old_preview_even_if_browser_forgets_to_discard(live_meter, change):
    observed = preview(live_meter).json()
    assert commit(live_meter, observed["preview_id"], frame(change)).status_code == 409
    client, state, _, _ = live_meter
    current = client.get(f"/api/sessions/{state.id}").json()
    assert current["step"] == 2 and "meter" not in current["checks"]


def test_background_outside_instrument_does_not_invalidate_matching_settings(live_meter):
    token = preview(live_meter).json()["preview_id"]
    assert commit(live_meter, token, frame("background")).json()["advanced"]


@pytest.mark.parametrize("variant", ["unknown", "false", "string", "range"])
def test_unknown_failed_nonboolean_and_inadequate_range_never_get_commit_token(live_meter, variant):
    response = live_meter[2]
    if variant == "range":
        response["meter_range"] = "dc_200mv"
    else:
        response["checks"]["red_in_voltage"] = {"unknown": None, "false": False, "string": "true"}[variant]
    observed = preview(live_meter).json()
    assert not observed["check"]["passed"] and observed["preview_id"] is None


def test_scene_discard_and_expiry_cannot_be_reused(live_meter):
    client, state, _, _ = live_meter
    token = preview(live_meter).json()["preview_id"]
    assert (
        client.post(f"/api/sessions/{state.id}/meter-live/discard", json={"preview_id": token}).status_code
        == 200
    )
    assert commit(live_meter, token).status_code == 409
    token = preview(live_meter).json()["preview_id"]
    client.app.state.meter_previews.items[token].started -= 31
    assert commit(live_meter, token).status_code == 409


@pytest.mark.parametrize("operation", ["pause", "switch", "delete", "revision"])
def test_paused_switched_deleted_or_changed_experiment_rejects_old_token(live_meter, operation):
    client, state, _, _ = live_meter
    token = preview(live_meter).json()["preview_id"]
    path = f"/api/sessions/{state.id}"
    if operation == "pause":
        client.post(path + "/actions", json={"kind": "pause"})
    elif operation == "switch":
        client.post("/api/sessions", json={})
    elif operation == "delete":
        client.request("DELETE", path, json={"confirmed": True, "expected_revision": state.revision})
    else:
        client.app.state.store.mutate(state.id, "test.change", {}, lambda _: {"accepted": True})
    assert commit(live_meter, token).status_code == (404 if operation == "delete" else 409)


def test_pause_during_inference_discards_result_before_a_token_is_issued(live_meter, monkeypatch):
    client, state, response, _ = live_meter

    def delayed(*args, **kwargs):
        client.app.state.store.pause_running("test pause during inference")
        return response

    monkeypatch.setattr(client.app.state.agent.gateway, "request", delayed)
    assert preview(live_meter).status_code == 409
    assert not client.app.state.meter_previews.items


@pytest.mark.parametrize("automatic", [False, True])
def test_enabled_live_meter_never_routes_legacy_page_requests_to_slow_cpu(live_meter, automatic):
    client, state, _, _ = live_meter
    response = client.post(
        f"/api/sessions/{state.id}/observe",
        data={"task": "step", "automatic": str(automatic).lower()},
        files={"file": ("frame.png", frame(), "image/png")},
    )
    assert response.status_code == 409 and "持续检查" in response.json()["detail"]


@pytest.mark.parametrize("code", ["YYY20", '{"checks":true}', "YYYY", "YYYV", "true", ""])
def test_compact_protocol_rejects_ambiguous_or_free_form_answers(code):
    with pytest.raises(ValueError):
        decode_reply(code)


def test_detector_must_localize_one_sufficiently_large_instrument():
    image = Image.new("RGB", (1920, 1080))
    assert meter_region(image, {"boxes": []}) == (None, None)
    assert meter_region(image, {"boxes": [{"label": "multimeter", "bbox": [0.1, 0.1, 0.01, 0.02]}]}) == (
        None,
        None,
    )
    box = {"label": "multimeter", "bbox": [0.1, 0.1, 0.3, 0.5]}
    assert meter_region(image, {"boxes": [box, box]}) == (None, None)


def test_closeup_views_are_preview_only_and_regions_are_kept_with_accepted_evidence(live_meter):
    client, state, response, _ = live_meter
    response.update(
        detail_mode="separate-dial-terminals-v1",
        detail_views=[
            {"part": "dial", "roi": [0.1, 0.25, 0.8, 0.45], "image_url": "data:image/jpeg;base64,fixture"},
            {
                "part": "terminals",
                "roi": [0.1, 0.70, 0.8, 0.25],
                "image_url": "data:image/jpeg;base64,fixture",
            },
        ],
    )
    result = preview(live_meter).json()
    assert result["detail_views"] == response["detail_views"]
    assert client.get(f"/api/sessions/{state.id}").json()["step"] == 2
    adopted = commit(live_meter, result["preview_id"]).json()
    fact = adopted["session"]["facts"]["workflow_check:meter"]["value"]
    assert fact["detail_mode"] == "separate-dial-terminals-v1"
    assert fact["detail_regions"] == [{"part": v["part"], "roi": v["roi"]} for v in response["detail_views"]]
    assert "image_url" not in json.dumps(fact)


def test_current_meter_delegation_does_not_display_historical_llm_limit(live_meter, monkeypatch):
    client, state, _, settings = live_meter
    agent = client.app.state.task_agent
    message = "本步自动尝试已达到上限，请人工核对或接管后继续。"
    old = {
        "step_id": state.plan["steps"][state.step]["id"],
        "tool": "ask_user",
        "arguments": json.dumps({"message": message}),
        "status": "completed",
        "reason": "old fixture",
        "model_used": 0,
        "id": "historical-limit-fixture",
    }
    monkeypatch.setattr(agent, "_latest", lambda _: old)
    current = client.get(f"/api/sessions/{state.id}").json()
    assert current["agent_decision"]["delegated_to"] == "meter_live"
    assert current["agent_decision"]["tool"] is None
    assert "上限" not in current["agent_decision"]["message"]
    assert "上限" not in current["interaction"]["message"]
    settings.meter_live_enabled = False
    legacy = client.get(f"/api/sessions/{state.id}").json()
    assert legacy["agent_decision"]["message"] == message  # Historical limit was not deleted or reset.


def test_latest_meter_diagnostic_is_read_only_and_distinguishes_skipped_model(live_meter):
    client, state, response, settings = live_meter
    path = f"/api/sessions/{state.id}"
    assert client.get(path + "/meter-live/status").json()["available"] is False
    before = client.get(path + "/events").json()
    response.update(
        checks={},
        meter_range=None,
        model_used=False,
        hint="仪表过小，请靠近镜头",
        diagnostic={"reason": "meter_detail_too_small", "meter_size": [160, 250]},
    )
    assert not preview(live_meter).json()["check"]["passed"]
    latest = client.get(path + "/meter-live/status").json()
    assert latest["current"] is True
    assert latest["last_preview"]["model_used"] is False
    assert latest["last_preview"]["diagnostic"]["reason"] == "meter_detail_too_small"
    assert latest["last_preview"]["check"]["message"] == response["hint"]
    assert "image_url" not in json.dumps(latest)
    assert client.get(path + "/events").json() == before
    assert not (settings.data_dir / "images").exists()
    client.post(path + "/actions", json={"kind": "pause"})
    assert client.get(path + "/meter-live/status").json()["current"] is False


def test_expired_meter_status_is_not_presented_as_current(live_meter):
    client, state, _, _ = live_meter
    preview(live_meter)
    cache = client.app.state.meter_previews.latest
    timestamp, result = cache[state.id]
    cache[state.id] = (timestamp - 61, result)
    assert client.get(f"/api/sessions/{state.id}/meter-live/status").json() == {
        "available": False,
        "current": False,
        "last_preview": None,
    }


def test_snapshot_acceptance_uses_one_stored_photo_without_comparing_later_video(live_meter, monkeypatch):
    from labagent import meter_live

    client, state, _, settings = live_meter
    monkeypatch.setattr(
        meter_live, "same_scene", lambda *_: pytest.fail("Snapshot mode must not compare video frames")
    )
    observed = preview(live_meter).json()
    assert observed["acceptance_mode"] == "single_frame_snapshot" and not observed["requires_fresh_frame"]
    path = f"/api/sessions/{state.id}/meter-live/accept-snapshot"
    result = client.post(path, json={"preview_id": observed["preview_id"]})
    assert result.status_code == 200 and result.json()["session"]["step"] == 3
    fact = result.json()["session"]["facts"]["workflow_check:meter"]["value"]
    assert fact["method"] == "single_frame_snapshot" and fact["captured_at"] == observed["captured_at"]
    assert "scene_verified_at" not in fact
    assert len(list((settings.data_dir / "images").glob("*.jpg"))) == 1
    assert client.post(path, json={"preview_id": observed["preview_id"]}).status_code == 409


@pytest.mark.parametrize("operation", ["pause", "revision", "expired", "delete"])
def test_snapshot_token_cannot_bypass_pause_revision_expiry_or_deletion(live_meter, operation):
    client, state, _, _ = live_meter
    token = preview(live_meter).json()["preview_id"]
    path = f"/api/sessions/{state.id}"
    if operation == "pause":
        client.post(path + "/actions", json={"kind": "pause"})
    elif operation == "revision":
        client.app.state.store.mutate(state.id, "test.change", {}, lambda _: {"accepted": True})
    elif operation == "expired":
        client.app.state.meter_previews.items[token].started -= 31
    else:
        client.request("DELETE", path, json={"confirmed": True, "expected_revision": state.revision})
    result = client.post(path + "/meter-live/accept-snapshot", json={"preview_id": token})
    assert result.status_code == (404 if operation == "delete" else 409)
