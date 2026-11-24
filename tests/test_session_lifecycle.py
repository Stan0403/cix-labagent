"""Experiment switches and confirmed deletion must not lose another experiment's data."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from labagent.main import create_app
from labagent.models import DomainError, Evidence, Measurement, NewSession, now


def seed_record(store, sid):
    def record(state):
        state.measurements.append(
            Measurement(value=1.34, source="manual", confidence=1, at=now(), saved=True)
        )
        state.facts["retained"] = Evidence(value="现场记录", source="manual")
        return {"accepted": True}

    state, _ = store.mutate(sid, "test.record", {}, record)
    store.message(sid, "user", "需要保留的对话")
    return state


def test_starting_a_new_experiment_pauses_old_and_preserves_all_records(client):
    store = client.app.state.store
    first = client.post("/api/sessions", json={}).json()
    before = seed_record(store, first["id"])
    events = store.events(before.id)
    second = client.post("/api/sessions", json={}).json()
    old = store.get(before.id)
    assert old.status == "paused" and old.revision == before.revision + 1
    assert old.measurements == before.measurements and old.facts == before.facts
    assert store.messages(old.id)[0]["content"] == "需要保留的对话"
    assert store.events(old.id)[:-1] == events
    assert second["status"] == "running" and len(store.list()) == 2


def test_refresh_and_history_review_pause_once_without_restarting(client):
    session = client.post("/api/sessions", json={}).json()
    path = "/api/sessions/" + session["id"]
    assert client.get(path).json()["revision"] == session["revision"]
    opened = client.post(path + "/open", json={}).json()
    assert opened["status"] == "paused" and "刷新" in opened["pause_reason"]
    assert client.post(path + "/open", json={}).json()["revision"] == opened["revision"]
    assert client.get(path).json()["step"] == session["step"]


def test_viewing_completed_history_pauses_previous_but_keeps_completed_state(client):
    store = client.app.state.store
    done = store.create(NewSession())
    store.mutate(
        done.id, "test.complete", {}, lambda s: setattr(s, "status", "completed") or {"accepted": True}
    )
    before = store.get(done.id)
    active = client.post("/api/sessions", json={}).json()
    viewed = client.post(f"/api/sessions/{done.id}/open", json={"previous_session_id": active["id"]}).json()
    assert viewed["status"] == "completed" and viewed["revision"] == before.revision
    assert store.get(active["id"]).status == "paused"


def test_resuming_an_old_experiment_atomically_pauses_the_new_one(client):
    first = client.post("/api/sessions", json={}).json()
    second = client.post("/api/sessions", json={}).json()
    path = "/api/sessions/" + first["id"]
    paused = client.get(path).json()
    result = client.post(
        path + "/actions", json={"kind": "resume", "confirmed": True, "expected_revision": paused["revision"]}
    ).json()
    assert result["accepted"] and result["session"]["status"] == "running"
    assert client.get("/api/sessions/" + second["id"]).json()["status"] == "paused"


def test_concurrent_new_sessions_leave_exactly_one_running(store):
    with ThreadPoolExecutor(max_workers=5) as executor:
        created = list(executor.map(lambda _: store.create(NewSession()), range(5)))
    states = [store.get(s.id) for s in created]
    assert sum(s.status == "running" for s in states) == 1
    assert sum(s.status == "paused" for s in states) == 4


def test_service_restart_pauses_saved_running_session_without_losing_data(client, settings):
    store = client.app.state.store
    old = seed_record(store, store.create(NewSession()).id)
    restarted = create_app(settings)
    after = restarted.state.store.get(old.id)
    assert after.status == "paused" and "重新启动" in after.pause_reason
    assert after.measurements == old.measurements
    assert restarted.state.store.messages(old.id) == store.messages(old.id)
    restarted.state.agent.gateway.close()


def test_delete_requires_confirmation_and_current_revision_and_keeps_other_records(client, settings):
    store = client.app.state.store
    first = seed_record(store, store.create(NewSession()).id)
    second = seed_record(store, store.create(NewSession()).id)
    path = "/api/sessions/" + first.id
    assert client.request("DELETE", path, json={"expected_revision": first.revision}).status_code == 422
    assert (
        client.request(
            "DELETE", path, json={"confirmed": True, "expected_revision": first.revision}
        ).status_code
        == 409
    )
    latest = store.get(first.id)
    images = settings.data_dir / "images"
    images.mkdir(exist_ok=True)
    shared = images / ("a" * 64 + ".jpg")
    shared.write_bytes(b"shared fixture resource")
    assert client.request(
        "DELETE", path, json={"confirmed": True, "expected_revision": latest.revision}
    ).json()["deleted"]
    assert client.get(path).status_code == 404 and shared.exists()
    assert store.get(second.id) == second and store.messages(second.id)
    assert all(s["id"] != first.id for s in client.get("/api/sessions").json())
    with store.connect() as db:
        for table in ("sessions", "events", "messages", "agent_runs"):
            column = "id" if table == "sessions" else "session_id"
            assert (
                db.execute(f"SELECT count(*) FROM {table} WHERE {column}=?", (first.id,)).fetchone()[0] == 0
            )


def agent_session(client, settings):
    settings.llm_base_url, settings.llm_model = "http://localhost:8081/v1", "test-fixture"
    store = client.app.state.store
    plan = client.app.state.plans.get("battery-voltage-15v").model_dump()
    state = store.create(NewSession(automation="agent"), plan)

    def prepare(s):
        s.step = 1
        s.checks[plan["steps"][0]["id"]] = Evidence(value=True, source="manual")
        return {"accepted": True}

    return store.mutate(state.id, "test.prepare", {}, prepare)[0]


@pytest.mark.parametrize("operation", ["switch", "delete"])
def test_model_result_arriving_after_switch_or_delete_cannot_advance_old_experiment(
    client, settings, monkeypatch, operation
):
    state = agent_session(client, settings)
    controller, store = client.app.state.task_agent, client.app.state.store
    entered, release = threading.Event(), threading.Event()

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return {
            "content": json.dumps(
                {"tool": "inspect_equipment", "arguments": {"mode": "fast"}, "reason": "fixture"}
            )
        }

    monkeypatch.setattr(controller.gateway, "chat", delayed)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(controller.decide, state.id, "delayed_lifecycle_test", state.revision)
        assert entered.wait(5)
        if operation == "switch":
            assert client.post("/api/sessions", json={}).status_code == 201
        else:
            assert (
                client.request(
                    "DELETE",
                    f"/api/sessions/{state.id}",
                    json={"confirmed": True, "expected_revision": state.revision},
                ).status_code
                == 200
            )
        release.set()
        with pytest.raises(DomainError) as error:
            pending.result(timeout=10)
        assert error.value.status == (409 if operation == "switch" else 404)
    with store.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM agent_runs WHERE session_id=?", (state.id,)).fetchone()[0] == 0
        )
    if operation == "switch":
        assert store.get(state.id).status == "paused" and store.get(state.id).step == state.step
