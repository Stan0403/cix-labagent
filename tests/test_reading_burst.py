import io
import json

import pytest
from PIL import Image

from .test_chat_input import prepare


def test_old_exhausted_retry_budget_and_waiting_ticket_do_not_block_click_reading(client, monkeypatch):
    from labagent.models import now
    state = prepare(client, readings=(0,))
    app = client.app
    settings = app.state.agent.settings
    settings.llm_base_url, settings.llm_model = "http://127.0.0.1:8081/v1", "test"
    with app.state.store.connect() as db:
        for i in range(settings.agent_max_actions + 1):
            db.execute(
                "INSERT INTO agent_runs (id,session_id,request_id,revision,step_id,fingerprint,tool,arguments,"
                "reason,status,created_at,model_used,result) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"old-{i}", state.id, f"old-request-{i}", state.revision,
                 state.plan["steps"][state.step]["id"], "", "ask_user" if i == settings.agent_max_actions else "read_display",
                 '{"message":"old wait"}' if i == settings.agent_max_actions else '{}',
                 "old", "proposed" if i == settings.agent_max_actions else "completed", now(), 0, "{}"))
    calls = []

    def choose(*args, **kwargs):
        calls.append(1)
        return {"content": json.dumps({"tool": "read_display", "arguments": {}, "reason": "准备读表"})}

    monkeypatch.setattr(app.state.agent.gateway, "chat", choose)
    path = f"/api/sessions/{state.id}"
    result = client.post(path + "/agent/decide", json={
        "expected_revision": state.revision, "request_id": "new-click-prepare"}).json()
    assert result["decision"]["tool"] == "read_display"
    assert result["session"]["interaction"]["primary_action"]["kind"] == "start_reading"
    again = client.post(path + "/agent/decide", json={
        "expected_revision": state.revision, "request_id": "poll-again"}).json()
    assert again["decision"]["id"] == result["decision"]["id"] and len(calls) == 1


@pytest.mark.parametrize("outcome", ["stable", "unclear", "unstable", "zero", "pause"])
def test_click_starts_one_bounded_reading_with_no_intermediate_llm(client, monkeypatch, outcome):
    state = prepare(client, readings=(1.34,))
    app = client.app
    settings = app.state.agent.settings
    settings.llm_base_url, settings.llm_model = "http://127.0.0.1:8081/v1", "test"
    calls, reads = [], []

    def choose(*args, **kwargs):
        calls.append(1)
        return {"content": json.dumps({"tool": "read_display", "arguments": {}, "reason": "采集电压"})}

    def vision(base, key, route, **kwargs):
        assert kwargs["timeout"] == 4
        assert kwargs["data"] == {"fast_only": "true"}
        if route == "locate_display":
            return {"roi": [.1, .1, .8, .8]}
        reads.append(1)
        value = None if outcome == "unclear" else 1.34 if len(reads) == 1 else 0 if outcome == "zero" else 1.6 if outcome == "unstable" else 1.34
        return {"text": json.dumps({"reading": value, "unit": "V"})}

    monkeypatch.setattr(app.state.agent.gateway, "chat", choose)
    monkeypatch.setattr(app.state.agent.gateway, "request", vision)
    path = f"/api/sessions/{state.id}"
    ready = client.post(path + "/agent/decide", json={
        "expected_revision": state.revision, "request_id": "prepare-reading"}).json()
    assert not reads and len(calls) == 1
    assert ready["session"]["interaction"]["primary_action"]["kind"] == "start_reading"
    if outcome == "pause":
        from labagent.models import Action
        observe = app.state.perform_observation

        def paused(*args, **kwargs):
            result = observe(*args, **kwargs)
            app.state.store.mutate(state.id, "test.pause", {}, lambda s:
                app.state.engine.apply(s, Action(kind="pause")))
            return result

        monkeypatch.setattr(app.state, "perform_observation", paused)
    buffer = io.BytesIO()
    Image.new("RGB", (128, 96)).save(buffer, format="JPEG")

    def capture():
        return client.post(path + "/agent/read-burst", data={
            "decision_id": ready["decision"]["id"], "first_id": "a" * 32, "second_id": "b" * 32},
            files={"first": ("a.jpg", buffer.getvalue(), "image/jpeg"),
                   "second": ("b.jpg", buffer.getvalue(), "image/jpeg")})

    response = capture()
    assert len(calls) == 1
    if outcome == "pause":
        assert response.status_code == 409
        assert app.state.store.get(state.id).status == "paused"
    else:
        assert response.status_code == 200, response.text
        result = response.json()
        assert "移开表笔" in result["message"]
        if outcome == "zero":
            assert "第1帧1.34 V" in result["message"] and "第2帧0 V" in result["message"]
            assert "读数为零" in result["message"]
        if outcome == "unstable":
            assert "第2帧1.6 V" in result["message"] and "0.02" in result["message"]
        assert len(reads) == (1 if outcome == "unclear" else 2)
        assert len(result["session"]["measurements"]) == (1 if outcome == "stable" else 0)
        again = capture()
        assert again.json()["session"]["revision"] == result["session"]["revision"]
        assert len(reads) == (1 if outcome == "unclear" else 2)
