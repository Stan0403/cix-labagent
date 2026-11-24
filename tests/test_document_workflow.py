import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from labagent.models import Action, Evidence, NewSession, ReadingCandidate
from labagent.plans import Plan, compile_document, extract_document
from labagent.readings import parse_candidate


def start(client):
    response = client.post("/api/sessions", json={"title": "battery", "plan_id": "battery-voltage-15v"})
    assert response.status_code == 201
    return response.json()


def act(client, state, kind, **data):
    result = client.post(f"/api/sessions/{state['id']}/actions", json={"kind": kind, **data}).json()
    return result["session"], result


def reach_measurement(client):
    state = start(client)
    for kind, data in [
        ("confirm_step", {"confirmed": True}), ("advance", {}),
        ("confirm_equipment", {"objects": ["battery", "multimeter", "probes"], "confirmed": True}), ("advance", {}),
        ("confirm_step", {"confirmed": True, "meter_range": "dc_20v"}), ("advance", {}),
    ]:
        state, result = act(client, state, kind, **data)
        assert result["accepted"], result
    assert state["steps"][state["step"]]["gate"] == "measurement"
    return state


@pytest.mark.parametrize("text,range_,expected", [
    ('{"reading":"1.534","unit":null}', "dc_20v", 1.534),
    ('{"reading":"-0.01","unit":"V"}', "dc_20v", -.01),
    ("0.000 V", "dc_2v", 0), ("125 mV", "dc_200mv", .125),
    ('The display shows the number "1.34".', "dc_20v", 1.34),
    ("OL", "dc_20v", None), ("1.53 and 20", "dc_20v", None),
    ("1534", "dc_2v", None), ('{"reading":null}', "dc_20v", None),
    ('{"reading":true}', "dc_20v", None), ("1.5", "", None),
])
def test_reading_ambiguity_sign_units_and_range(text, range_, expected):
    assert parse_candidate(text, range_)["value"] == expected


def test_battery_full_real_workflow_with_provenance(client):
    state = reach_measurement(client)
    store = client.app.state.store
    candidate = ReadingCandidate(value=1.53, raw_text='{"reading":"1.53"}', reference="image:"+"a"*64,
                                 step_id="measure", meter_range="dc_20v", roi=[0, 0, 1, 1])
    store.mutate(state["id"], "test.candidate", {}, lambda s: (s.candidates.append(candidate) or {"accepted": True}))
    state, denied = act(client, state, "confirm_candidate", candidate_id=candidate.id)
    assert not denied["accepted"] and not state["measurements"]
    state, accepted = act(client, state, "confirm_candidate", candidate_id=candidate.id, value=1.52, confirmed=True)
    assert accepted["accepted"]
    measurement = state["measurements"][0]
    assert measurement["value"] == 1.52 and measurement["original_value"] == 1.53
    assert measurement["source"] == "vision_confirmed" and measurement["reference"].endswith("a"*64)
    state, duplicate = act(client, state, "confirm_candidate", candidate_id=candidate.id, confirmed=True)
    assert not duplicate["accepted"] and len(state["measurements"]) == 1
    for kind, data in [("advance", {}), ("save_measurement", {"confirmed": True})]:
        state, result = act(client, state, kind, **data)
        assert result["accepted"], result
    assert state["interaction"]["phase"] == "notice"
    store.mutate(state["id"], "test.notice_elapsed", {}, lambda s:setattr(s,"completion_notice_until",(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()))
    for kind in ("advance","report"):
        state,result=act(client,state,kind)
        assert result["accepted"],result
    assert state["status"] == "completed" and state["measurements"][0]["saved"]
    report = client.get(f"/api/sessions/{state['id']}/export").text
    assert "1.52" in report and "1.53" in report and "battery_voltage.md" in report
    assert candidate.id not in report and "人工修正" in report and "修正说明未记录" in report


def test_missing_equipment_and_meter_cannot_be_skipped(client):
    state = start(client)
    state, _ = act(client, state, "confirm_step", confirmed=True)
    state, _ = act(client, state, "advance")
    state, _ = act(client, state, "confirm_equipment", confirmed=True, objects=["battery", "probes"])
    state, result = act(client, state, "advance")
    assert not result["accepted"] and state["steps"][state["step"]]["gate"] == "equipment"
    state, _ = act(client, state, "confirm_equipment", confirmed=True, objects=["battery", "probes", "multimeter"])
    state, _ = act(client, state, "advance")
    state, result = act(client, state, "confirm_step", confirmed=True)
    assert not result["accepted"]
    state, result = act(client, state, "advance")
    assert not result["accepted"]


def test_reading_observation_is_candidate_not_measurement(client, monkeypatch):
    state = reach_measurement(client)
    gateway = client.app.state.agent.gateway
    monkeypatch.setattr(gateway, "request", lambda *a, **kw: {"text": '{"reading":"1.34","unit":null}', "backend": "qwen25-vl-mnn-cpu"})
    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buffer, format="PNG")
    result = client.post(f"/api/sessions/{state['id']}/observe", files={"file": ("lcd.png", buffer.getvalue(), "image/png")},
                         data={"task": "reading", "roi": "0,0,1,1"})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["candidate"]["value"] == 1.34 and not data["session"]["measurements"]
    image = client.get(f"/api/sessions/{state['id']}/candidates/{data['candidate']['id']}/image")
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"


def test_stale_candidate_and_cross_session_id_rejected(client):
    state = reach_measurement(client)
    store = client.app.state.store
    c = ReadingCandidate(value=1.5, step_id="measure", at=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat())
    store.mutate(state["id"], "test.candidate", {}, lambda s: (s.candidates.append(c) or {}))
    state, result = act(client, state, "confirm_candidate", candidate_id=c.id, confirmed=True)
    assert not result["accepted"] and not state["measurements"]
    other = reach_measurement(client)
    other, result = act(client, other, "confirm_candidate", candidate_id=c.id, confirmed=True)
    assert not result["accepted"] and not other["measurements"]


def test_recheck_invalidates_setup_and_pause_cannot_be_bypassed(client):
    state = reach_measurement(client)
    state, result = act(client, state, "invalidate_wiring")
    assert result["accepted"] and state["status"] == "paused" and "meter_range" not in state["facts"]
    state, result = act(client, state, "manual_measurement", value=1.5, confirmed=True)
    assert not result["accepted"]
    state, result = act(client, state, "remeasure")
    assert not result["accepted"]


DOCUMENT = """观察水的自然降温过程
实验器材：温度计、烧杯
实验步骤
1. 准备器材，并由教师提供温水。
2. 观察温度计与水面状态，按文档记录观察现象。
3. 整理桌面，确认操作完成。
"""


def test_document_plan_varies_in_length_requires_review_and_keeps_snapshot(client):
    response = client.post("/api/plans/import", data={"requirements": DOCUMENT, "method": "rules"})
    assert response.status_code == 200, response.text
    p = response.json()
    assert p["approved"] is False and len(p["steps"]) != 8
    assert p["steps"][2]["source_line"] == 4
    assert client.post("/api/sessions", json={"plan_id": p["id"]}).status_code == 409
    assert client.post(f"/api/plans/{p['id']}/approve", json={"confirmed": True}).status_code == 200
    state = client.post("/api/sessions", json={"plan_id": p["id"]}).json()
    original = state["steps"][2]["instruction"]
    p["steps"][2]["instruction"] = "修改后的新要求"
    assert client.put(f"/api/plans/{p['id']}", json=p).status_code == 200
    assert client.get(f"/api/sessions/{state['id']}").json()["steps"][2]["instruction"] == original


@pytest.mark.parametrize("scenario,status", [("happy", "completed"), ("missing_equipment", "running"), ("unconfirmed_meter", "running")])
def test_rehearsals_separate_from_real_data(client, scenario, status):
    response = client.post("/api/plans/battery-voltage-15v/rehearse", json={"scenario": scenario})
    assert response.status_code == 200, response.text
    state = response.json()["session"]
    assert state["mode"] == "replay" and state["status"] == status
    assert all(m["source"] == "replay" for m in state["measurements"])


def test_procedure_without_measurement_finishes_and_gets_fast_guidance(client):
    p = client.post("/api/plans/import", data={"requirements": DOCUMENT, "method": "rules"}).json()
    client.post(f"/api/plans/{p['id']}/approve", json={"confirmed": True})
    state = client.post("/api/sessions", json={"plan_id": p["id"]}).json()
    answer = client.post(f"/api/sessions/{state['id']}/chat", json={"message": "现在做到哪一步了？"})
    assert answer.status_code == 200 and answer.json()["engine"] == "workflow"
    result = client.post(f"/api/plans/{p['id']}/rehearse", json={"scenario": "happy"}).json()
    assert result["session"]["status"] == "completed" and not result["session"]["measurements"]


def test_invalid_plan_and_unreadable_documents_rejected():
    with pytest.raises(Exception):
        extract_document("scan.txt", b"x")
    with pytest.raises(Exception):
        compile_document("Only a general title with no operational steps provided.", "x.txt")


def test_edge_mode_blocks_remote_models_before_any_network_request(tmp_path):
    from labagent.config import Settings
    from labagent.providers import Gateway, ProviderError
    settings = Settings(data_dir=tmp_path, local_only=True)
    gateway = Gateway(settings)
    try:
        with pytest.raises(ProviderError, match="本机"):
            gateway.request("https://example.com/v1", "", "chat/completions", json={})
    finally:
        gateway.close()


def test_docx_and_pdf_text_extraction():
    from zipfile import ZipFile
    from html import escape
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    document = io.BytesIO()
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    xml += ''.join('<w:p><w:r><w:t>'+escape(line)+'</w:t></w:r></w:p>' for line in DOCUMENT.splitlines())
    xml += '</w:body></w:document>'
    with ZipFile(document, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    assert '观察水' in extract_document('steps.docx', document.getvalue())
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=400)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 350 Td (Battery voltage experiment requirements) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    assert 'Battery voltage' in extract_document('steps.pdf', buffer.getvalue())


def test_reported_hazard_pauses_without_waiting_for_model(client, monkeypatch):
    state = reach_measurement(client)
    def forbidden(*args, **kwargs):
        raise AssertionError("hazard must not wait for model inference")
    monkeypatch.setattr(client.app.state.agent.gateway, "chat", forbidden)
    result = client.post(f"/api/sessions/{state['id']}/chat", json={"message": "电池发热，我该怎么办"})
    assert result.status_code == 200 and result.json()["session"]["status"] == "paused"
    assert "教师" in result.json()["answer"]


def test_local_model_operations_are_validated_and_never_auto_approved(client, monkeypatch):
    gateway = client.app.state.agent.gateway
    generated = {"title": "万用表测量1.5V干电池", "equipment": ["电池", "万用表", "表笔"],
                 "operations": ["核对插孔COM和V/Ω及直流20V档", "红正黑负接触电池", "读取电压", "保存读数", "移开表笔关闭仪表并生成报告"],
                 "approved": True}
    monkeypatch.setattr(gateway, "request", lambda *args, **kwargs: {"choices": [{"message": {"content": json.dumps(generated)}}]})
    response = client.post("/api/plans/import", data={"requirements": "请使用万用表测量1.5V干电池电压，按要求操作并记录，最后移开表笔。", "method": "llm"})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["compiler"] == "local_llm" and plan["approved"] is False
    assert any("移开表笔" in s["instruction"] for s in plan["steps"])
    assert all(s["source_line"] is None for s in plan["steps"] if s["origin"] == "model_suggestion")


def test_generic_measurement_requires_reviewed_unit(client):
    text = "直尺测量长度\n实验器材：直尺、记录纸\n实验步骤\n1. 将直尺与物体对齐。\n2. 读取长度读数。\n3. 保存读数。"
    response = client.post("/api/plans/import", data={"requirements": text, "method": "rules"})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["measurement"]["adapter"] == "manual"
    assert client.post(f"/api/plans/{plan['id']}/approve", json={"confirmed": True}).status_code == 422
    plan["measurement"].update(unit="cm", min_value=0, max_value=100)
    assert client.put(f"/api/plans/{plan['id']}", json=plan).status_code == 200
    assert client.post(f"/api/plans/{plan['id']}/approve", json={"confirmed": True}).status_code == 200


def test_workflow_observes_missing_items_then_requires_another_frame_or_manual_correction(client, monkeypatch):
    state = start(client)
    state, _ = act(client, state, "confirm_step", confirmed=True)
    assert state["agent_decision"]["tool"] == "advance"
    state, _ = act(client, state, "advance", automatic=True)
    assert state["agent_decision"]["tool"] == "inspect_equipment"
    gateway = client.app.state.agent.gateway
    response = {"present": {"battery": False, "multimeter": True, "probes": None},
                "boxes": [{"label": "multimeter", "bbox": [.2, .1, .4, .7]}, {"label": "battery", "bbox": [-1, 0, 2, 2]}]}
    monkeypatch.setattr(gateway, "request", lambda *args, **kwargs: response)
    image = io.BytesIO(); Image.new("RGB", (200, 100)).save(image, format="PNG")
    path = f"/api/sessions/{state['id']}/observe"
    result = client.post(path, files={"file": ("scene.png", image.getvalue(), "image/png")}, data={"task": "objects", "automatic": "true"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert len(body["boxes"]) == 1 and "电池" in body["session"]["agent_decision"]["message"]
    assert body["session"]["agent_decision"]["tool"] == "inspect_equipment"
    response["present"] = {"battery": True, "multimeter": True, "probes": True}
    result = client.post(path, files={"file": ("scene.png", image.getvalue(), "image/png")}, data={"task": "objects", "automatic": "true"}).json()
    assert result["session"]["agent_decision"]["autorun"] is True
    assert result["session"]["missing_objects"]
    state, _ = act(client, result["session"], "confirm_equipment", objects=["battery", "multimeter", "probes"], confirmed=True)
    assert state["agent_decision"]["tool"] == "advance"


def test_automatic_actions_cannot_attest_physical_facts(client):
    state = start(client)
    response = client.post(f"/api/sessions/{state['id']}/actions", json={"kind": "confirm_step", "confirmed": True, "automatic": True})
    assert response.status_code == 422
    assert not client.get(f"/api/sessions/{state['id']}").json()["checks"]


def test_auto_display_localization_and_reading_are_two_real_tool_calls(client, monkeypatch):
    state = reach_measurement(client)
    calls = []
    def request(base, key, path, **kwargs):
        calls.append(path)
        return {"roi": [.1, .1, .8, .8]} if path == "locate_display" else {"text": '{"reading":"1.34","unit":null}'}
    monkeypatch.setattr(client.app.state.agent.gateway, "request", request)
    image = io.BytesIO(); Image.new("RGB", (200, 100)).save(image, format="PNG")
    response = client.post(f"/api/sessions/{state['id']}/observe", files={"file": ("scene.png", image.getvalue(), "image/png")}, data={"task": "reading", "automatic": "true"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert calls == ["locate_display", "recognize"] and body["auto_roi"] is True
    assert body["candidate"]["value"] == 1.34 and not body["session"]["measurements"]


def test_equipment_boxes_reject_invalid_or_unknown_coordinates():
    from labagent.readings import equipment_result
    result = equipment_result({"present": {"battery": True, "multimeter": False}, "boxes": [
        {"label": "battery", "bbox": [100, 200, 300, 600]},
        {"label": "multimeter", "bbox": [100, 100, 200, 200]},
        {"label": "unknown", "bbox": [100, 100, 200, 200]},
        {"label": "battery", "bbox": [900, 100, 100, 200]},
    ]}, {"battery": "电池", "multimeter": "万用表"})
    assert result["boxes"] == [{"label": "battery", "bbox": [.1, .2, .2, .4]}]


def test_direct_vlm_pixel_boxes_keep_unknowns_and_reject_bad_coordinates():
    from labagent.readings import parse_vlm_detections
    labels = {"battery": "电池", "multimeter": "万用表", "probes": "表笔"}
    raw = json.dumps([{"label": "multimeter", "bbox_2d": [200, 100, 600, 400]},
                      {"label": "battery", "bbox_2d": [-50, 10, 30, 40]}])
    result = parse_vlm_detections(raw, labels, 1000, 500)
    assert result["present"] == {"battery": None, "multimeter": True, "probes": None}
    assert result["boxes"] == [{"label": "multimeter", "bbox": [.2, .2, .4, .6]}]
    assert parse_vlm_detections('```json\n[]\n```', labels, 1000, 500)["boxes"] == []
