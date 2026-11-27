import io
import json

import pytest
from PIL import Image

from labagent.providers import ProviderError
from .test_chat_input import prepare


@pytest.mark.parametrize("value", [1.34, 0, -1.34, None, 3.0])
def test_one_frame_records_actual_number_without_a_second_capture(client, monkeypatch, value):
    state = prepare(client, readings=(1.30,))
    app = client.app
    settings = app.state.agent.settings
    settings.llm_base_url, settings.llm_model = "http://127.0.0.1:8081/v1", "test"
    calls, recognized, locations = [], [], []

    def choose(*args, **kwargs):
        calls.append(1)
        return {"content": json.dumps({"tool": "read_display", "arguments": {}, "reason": "读取电压"})}

    def vision(base, key, route, **kwargs):
        assert kwargs["timeout"] == 8
        assert kwargs["data"] == {"fast_only": "true"}
        if route == "locate_display":
            locations.append(1)
            if len(locations) == 1:
                raise ProviderError("定位等待超时")
            if len(locations) == 2:
                return {"roi": None, "meter_present": False}
            return {"roi": [.1, .1, .8, .8]}
        recognized.append(1)
        return {"text": json.dumps({"reading": value, "unit": "V"})}

    monkeypatch.setattr(app.state.agent.gateway, "chat", choose)
    monkeypatch.setattr(app.state.agent.gateway, "request", vision)
    path = f"/api/sessions/{state.id}"
    ticket = client.post(path + "/agent/decide", json={
        "expected_revision": state.revision, "request_id": "single-frame-prepare"}).json()["decision"]
    buffer = io.BytesIO()
    Image.new("RGB", (128, 96)).save(buffer, format="JPEG")

    def capture():
        result = client.post(path + "/agent/read-frame", data={
            "decision_id": ticket["id"], "capture_id": "c" * 32},
            files={"file": ("frame.jpg", buffer.getvalue(), "image/jpeg")})
        assert result.status_code == 200, result.text
        return result.json()

    first = capture()
    assert not first["located"] and first["location_error"]
    second = capture()
    assert not second["located"] and second["meter_present"] is False
    assert second["session"]["revision"] == state.revision
    assert not recognized
    result = capture()
    assert len(recognized) == len(calls) == 1 and result["single_frame"]
    expected = value is not None and abs(value) <= 2
    assert len(result["session"]["measurements"]) == int(expected)
    if expected:
        measurement = result["session"]["measurements"][0]
        assert measurement["value"] == value
        assert measurement["supporting_candidate_ids"] == ["capture_" + "c" * 32]
        assert result["candidate"]["confirmation_method"] == "single_frame"
    again = capture()
    assert again["session"]["revision"] == result["session"]["revision"]
    assert len(locations) == 3 and len(recognized) == 1
