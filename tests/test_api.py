import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from labagent.config import Settings
from labagent.main import create_app

from .conftest import measurement_stage


def new(client, **kwargs):
    result = client.post("/api/sessions", json={"title": "test", **kwargs})
    assert result.status_code == 201
    return result.json()


def test_health_and_frontend(client):
    assert client.get("/api/health").json()["providers"]["vision_provider"] == "disabled"
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert "Content-Security-Policy" in client.get("/").headers


def test_session_persistence_and_audit(client):
    s = new(client)
    result = client.post(f"/api/sessions/{s['id']}/actions", json={"kind": "advance"}).json()
    assert not result["accepted"] and result["session"]["step"] == 0
    assert client.get(f"/api/sessions/{s['id']}").json()["revision"] == 1
    assert len(client.get(f"/api/sessions/{s['id']}/events").json()) == 2


def test_stale_revision_conflict(client):
    s = new(client)
    url = f"/api/sessions/{s['id']}/actions"
    assert client.post(url, json={"kind": "read_sop", "confirmed": True, "expected_revision": 0}).status_code == 200
    result = client.post(url, json={"kind": "advance", "expected_revision": 0})
    assert result.status_code == 409
    assert client.get(f"/api/sessions/{s['id']}").json()["step"] == 0


def test_invalid_actions_and_numbers_rejected(client):
    s = new(client)
    url = f"/api/sessions/{s['id']}/actions"
    assert client.post(url, json={"kind": "execute_shell", "command": "bad"}).status_code == 422
    assert client.post(url, content='{"kind":"manual_measurement","value":1e999}',
                       headers={"Content-Type": "application/json"}).status_code == 422
    assert client.post(url, json={"kind": "advance", "source": "replay"}).status_code == 422


def test_progress_question_does_not_advance(client):
    s = new(client)
    result = client.post(f"/api/sessions/{s['id']}/chat", json={"message": "现在做到哪一步了？"}).json()
    assert result["session"]["step"] == 0 and result["session"]["revision"] == 0
    assert "阅读实验说明" in result["answer"] and result["engine"] == "rules"
    assert len(client.get(f"/api/sessions/{s['id']}/messages").json()) == 2


def test_question_with_next_is_not_a_command(client):
    s = new(client)
    result = client.post(f"/api/sessions/{s['id']}/chat", json={"message": "为什么不能进入下一步？"}).json()
    assert not result["traces"] and result["session"]["revision"] == 0


def test_command_obeys_rules(client):
    s = new(client)
    result = client.post(f"/api/sessions/{s['id']}/chat", json={"message": "下一步"}).json()
    assert not result["traces"][0]["result"]["accepted"] and result["session"]["step"] == 0


def test_real_retrieval_and_missing_source(client):
    result = client.get("/api/knowledge/search", params={"q": "为什么断电接线"}).json()
    assert result["results"] and all(item["source"].endswith(".md") for item in result["results"])
    assert "line" in result["results"][0]
    assert client.get("/api/knowledge/search", params={"q": "zxqvbnmasdf"}).json()["results"] == []


def test_knowledge_upload_sanitizes_paths(client):
    result = client.post("/api/knowledge/upload", files={"file": ("../../outside.md", "特殊电压校准流程", "text/plain")})
    assert result.status_code == 200 and "/" not in result.json()["source"]
    assert client.get("/api/knowledge/search", params={"q": "特殊电压校准"}).json()["results"]
    assert client.post("/api/knowledge/upload", files={"file": ("bad.pdf", b"bad", "application/pdf")}).status_code == 422


def test_replay_complete_is_separate_and_report_labelled(client):
    real = new(client)
    replay = client.post("/api/fixtures/happy_path", json={}).json()
    s = replay["session"]
    assert s["mode"] == "replay" and s["status"] == "completed"
    assert all(item["accepted"] for item in replay["results"])
    assert s["measurements"][0]["source"] == "replay"
    assert client.get(f"/api/sessions/{real['id']}").json()["step"] == 0
    report = client.get(f"/api/sessions/{s['id']}/export").text
    assert "非真实测量" in report and "1.25" in report
    assert "replay" in client.get(f"/api/sessions/{s['id']}/export?format=csv").text
    exported = client.get(f"/api/sessions/{s['id']}/export?format=json").json()
    assert len(exported["events"]) == 18


@pytest.mark.parametrize("fixture,step", [("missing_equipment", 2), ("unsafe_order", 1)])
def test_abnormal_replays_block(client, fixture, step):
    result = client.post("/api/fixtures/" + fixture, json={}).json()
    assert result["session"]["step"] == step and not result["results"][-1]["accepted"]


def test_invalid_replay_validated_before_session_created(client):
    result = client.post("/api/replays", json={"name": "bad", "events": [{"kind": "power", "data": {"value": "yes"}}]})
    assert result.status_code == 422 and client.get("/api/sessions").json() == []


def image_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_unconfigured_vision_never_fakes_results(client):
    s = new(client)
    result = client.post(f"/api/sessions/{s['id']}/image", data={"task": "objects"},
                         files={"file": ("sample.png", image_bytes(), "image/png")})
    assert result.status_code == 503
    current = client.get(f"/api/sessions/{s['id']}").json()
    assert not current["facts"] and not current["measurements"]


def test_corrupt_image_rejected(client):
    s = new(client)
    result = client.post(f"/api/sessions/{s['id']}/image", files={"file": ("bad.png", b"not a png", "image/png")})
    assert result.status_code == 503


def test_asr_not_configured(client):
    result = client.post("/api/audio/transcribe", files={"file": ("record.webm", b"audio", "audio/webm")})
    assert result.status_code == 503 and "未配置" in result.json()["detail"]


def test_asr_file_type_rejected(client):
    assert client.post("/api/audio/transcribe", files={"file": ("x", b"a", "application/octet-stream")}).status_code == 422


def test_image_observation_integration(client, monkeypatch):
    s = new(client)
    sid = s["id"]
    store, engine = client.app.state.store, client.app.state.engine
    store.mutate(sid, "test.prepare", {}, lambda current: (measurement_stage(engine, current), {})[1])
    monkeypatch.setattr(client.app.state.perception, "read_voltage", lambda image, roi:
                        {"value": 1.25, "unit": "V", "confidence": .98})
    result = client.post(f"/api/sessions/{sid}/image", data={"task": "ocr", "roi": "0,0,1,1"},
                         files={"file": ("sample.png", image_bytes(), "image/png")}).json()
    assert result["accepted"]
    m = result["session"]["measurements"][0]
    assert m["source"] == "ocr" and not m["saved"] and m["reference"].startswith("image:")


def test_serial_ingestion_and_bounds(client):
    sid = new(client)["id"]
    store, engine = client.app.state.store, client.app.state.engine
    store.mutate(sid, "test.prepare", {}, lambda s: (measurement_stage(engine, s), {})[1])
    result = client.post(f"/api/sessions/{sid}/sensor", json={"value": 1.2, "device": "COM3"}).json()
    assert result["accepted"] and result["session"]["measurements"][0]["source"] == "serial"
    result = client.post(f"/api/sessions/{sid}/sensor", json={"value": 5, "device": "COM3"}).json()
    assert not result["accepted"] and len(result["session"]["measurements"]) == 1


def test_low_confidence_image_does_not_create_measurement(client, monkeypatch):
    sid = new(client)["id"]
    store, engine = client.app.state.store, client.app.state.engine
    store.mutate(sid, "test.prepare", {}, lambda s: (measurement_stage(engine, s), {})[1])
    monkeypatch.setattr(client.app.state.perception, "read_voltage", lambda image, roi:
                        {"value": 1.2, "unit": "V", "confidence": .2})
    result = client.post(f"/api/sessions/{sid}/image", data={"task": "ocr", "roi": "0,0,1,1"},
                         files={"file": ("sample.png", image_bytes(), "image/png")}).json()
    assert not result["accepted"] and result["session"]["measurements"] == []


def test_expired_evidence_reflected_on_get(client):
    sid = new(client)["id"]
    store, engine = client.app.state.store, client.app.state.engine
    def prepare(s):
        measurement_stage(engine, s)
        s.facts["power"].at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        return {}
    store.mutate(sid, "test.prepare", {}, prepare)
    assert any("上电" in text for text in client.get(f"/api/sessions/{sid}").json()["blockers"])


def test_origins_and_host_are_restricted(client):
    assert client.post("/api/sessions", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403


def test_large_request_rejected_before_parse(client):
    assert client.post("/api/sessions", content="{}", headers={"Content-Length": "19000000"}).status_code == 413


def test_missing_session(client):
    assert client.get("/api/sessions/not-existing").status_code == 404
    assert client.get("/api/fixtures").status_code == 200
    assert client.post("/api/fixtures/not-existing", json={}).status_code == 404


def test_token_protection_and_secrets_not_exposed(tmp_path):
    from fastapi.testclient import TestClient
    settings = Settings(data_dir=tmp_path, access_token="local-secret", llm_api_key="model-secret")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").status_code == 401
        response = client.get("/api/health", headers={"Authorization": "Bearer local-secret"})
        assert response.status_code == 200
        assert "model-secret" not in response.text and "local-secret" not in response.text


def test_nonlocal_host_requires_token(tmp_path):
    with pytest.raises(ValueError):
        create_app(Settings(data_dir=tmp_path, host="0.0.0.0"))
