"""Routing and coordinate contracts for the optional equipment-only NPU path."""
import importlib.util
import io
import json
from pathlib import Path

from fastapi import HTTPException
from PIL import Image
import pytest
from starlette.datastructures import UploadFile


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.delenv("LABAGENT_EQUIPMENT_VLM_SERVER", raising=False)
    path = Path(__file__).resolve().parents[1] / "scripts/qwen3vl_gguf_service.py"
    spec = importlib.util.spec_from_file_location("equipment_adapter_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "EQUIPMENT_SERVER", "http://127.0.0.1:8034")
    return module


def upload(size=(200, 100)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return UploadFile(file=io.BytesIO(buffer.getvalue()), filename="scene.png")


@pytest.mark.parametrize("size,model_box,expected", [
    ((200, 100), [250, 312.5, 750, 687.5], [.25, .125, .5, .75]),
    ((100, 200), [312.5, 250, 687.5, 750], [.125, .25, .75, .5]),
])
def test_equipment_boxes_align_with_original_wide_and_tall_images(adapter, monkeypatch, size, model_box, expected):
    calls = []

    def infer(image, instruction, **kwargs):
        calls.append((image.size, kwargs))
        return json.dumps([{"label": "multimeter", "bbox_2d": model_box}])

    monkeypatch.setattr(adapter, "infer", infer)
    result = adapter.inspect(upload(size), labels='{"multimeter":"万用表"}')
    assert result["boxes"][0]["bbox"] == pytest.approx(expected)
    assert result["backend"] == adapter.EQUIPMENT_BACKEND
    assert calls[0][0] == (320, 320)
    assert calls[0][1]["server"] == adapter.EQUIPMENT_SERVER


def test_box_in_padding_does_not_establish_equipment_presence(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "infer", lambda *a, **kw: '[{"label":"multimeter","bbox_2d":[0,0,200,200]}]')
    result = adapter.inspect(upload(), labels='{"multimeter":"万用表"}')
    assert result["present"]["multimeter"] is None and not result["boxes"]


def test_missing_black_probe_is_not_a_complete_pair_on_npu_route(adapter, monkeypatch):
    calls = []

    def infer(image, instruction, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return json.dumps([{"label": "__labagent_red_0", "bbox_2d": [500, 300, 600, 650]},
                               {"label": "__labagent_black_0", "present": False}])
        return '{"handheld_probe":true,"color":"red"}'

    monkeypatch.setattr(adapter, "infer", infer)
    result = adapter.inspect(upload(), labels='{"probes":"红黑表笔"}')
    assert result["present"]["probes"] is False and not result["boxes"]
    assert len(calls) == 2 and all(c["server"] == adapter.EQUIPMENT_SERVER for c in calls)


def test_reading_and_step_checks_keep_the_cpu_route(adapter, monkeypatch):
    calls = []

    def infer(image, instruction, **kwargs):
        calls.append(kwargs)
        return '{"reading":null,"unit":null}' if len(calls) == 1 else '{"checks":{}}'

    monkeypatch.setattr(adapter, "infer", infer)
    reading = adapter.recognize(upload())
    checks = adapter.check_step(upload(), rule="meter_setup")
    assert reading["backend"] == checks["backend"] == adapter.BACKEND
    assert all(not c.get("server") and not c.get("fixed_grid") for c in calls)


def test_npu_failure_is_not_reported_as_successful_cpu_inference(adapter, monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs)
        raise HTTPException(503, "Encoder unavailable")

    monkeypatch.setattr(adapter, "infer", fail)
    with pytest.raises(HTTPException) as error:
        adapter.inspect(upload(), labels='{"multimeter":"万用表"}')
    assert error.value.status_code == 503 and len(calls) == 1


def test_unconfigured_equipment_path_preserves_cpu_geometry(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "EQUIPMENT_SERVER", "")
    calls = []

    def infer(image, instruction, **kwargs):
        calls.append(image.size)
        return '[{"label":"multimeter","bbox_2d":[250,125,750,875]}]'

    monkeypatch.setattr(adapter, "infer", infer)
    result = adapter.inspect(upload(), labels='{"multimeter":"万用表"}')
    assert calls == [(200, 100)]
    assert result["boxes"][0]["bbox"] == pytest.approx([.25, .125, .5, .75])
    assert result["backend"] == adapter.BACKEND
