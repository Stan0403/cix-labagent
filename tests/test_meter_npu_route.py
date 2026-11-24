import importlib.util
import io
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image
from starlette.datastructures import UploadFile

from labagent import meter_live_model


@pytest.fixture
def adapter(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/qwen3vl_gguf_service.py"
    spec = importlib.util.spec_from_file_location("meter_adapter_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.METER_SERVER = "http://127.0.0.1:8034"
    module.YOLO_SERVER = "http://127.0.0.1:8035"
    return module


def image():
    data = io.BytesIO()
    Image.new("RGB", (480, 640)).save(data, format="PNG")
    return UploadFile(file=io.BytesIO(data.getvalue()), filename="frame.png")


def test_no_meter_does_not_run_vlm_or_invent_a_range(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "inspect_yolo", lambda *a: {"boxes": []})
    monkeypatch.setattr(
        meter_live_model, "infer_closeups", lambda *a: pytest.fail("No meter must not invoke VLM")
    )
    result = adapter.check_meter_live(image())
    assert not result["model_used"] and result["meter_range"] is None
    assert all(v is None for v in result["checks"].values())


@pytest.mark.parametrize("encoded", [False, True])
def test_meter_route_requires_actual_encoder_use_and_releases_lock(adapter, monkeypatch, encoded):
    monkeypatch.setattr(
        adapter, "inspect_yolo", lambda *a: {"boxes": [{"label": "multimeter", "bbox": [0, 0, 1, 1]}]}
    )
    states = iter([{"requests": 7}, {"requests": 9 if encoded else 8, "last_duration_ms": 480}])
    monkeypatch.setattr(adapter, "meter_worker_state", lambda: next(states))

    def compact(crop, server, opener):
        assert server == "http://127.0.0.1:8034"
        return {
            **meter_live_model.decode_reply("Y?Yv"),
            "detail_views": [
                {"part": "dial", "roi": [0, 0.26, 1, 0.53]},
                {"part": "terminals", "roi": [0, 0.67, 1, 0.33]},
            ],
        }

    monkeypatch.setattr(meter_live_model, "infer_closeups", compact)
    if encoded:
        result = adapter.check_meter_live(image())
        assert (
            result["model_used"] and result["vision_ms"] == 480 and result["checks"]["red_in_voltage"] is None
        )
    else:
        with pytest.raises(HTTPException) as error:
            adapter.check_meter_live(image())
        assert error.value.status_code == 503
    assert not adapter.METER_LOCK.locked()
