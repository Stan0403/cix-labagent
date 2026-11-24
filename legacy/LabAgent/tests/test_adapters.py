import json
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from PIL import Image

from labagent.config import Settings
from labagent.perception import Perception
from labagent.providers import Gateway, ProviderError


class Session:
    def __init__(self, output):
        self.output = output
        self.inputs = None

    def get_inputs(self):
        return [SimpleNamespace(name="images", shape=[1, 3, 640, 640], type="tensor(float)")]

    def get_providers(self):
        return ["CPUExecutionProvider"]

    def run(self, _, inputs):
        self.inputs = inputs
        return [self.output]


def test_onnx_postprocessing_and_real_preprocessing(settings):
    # This tests the detector adapter, NOT the accuracy of a trained network.
    output = np.zeros((1, 8, 3), dtype=np.float32)
    output[0, :, 0] = [320, 320, 100, 100, .95, 0, 0, 0]
    output[0, :, 1] = [321, 321, 100, 100, .9, 0, 0, 0]
    output[0, :, 2] = [100, 200, 80, 60, 0, .99, 0, 0]
    session = Session(output)
    settings.vision_provider = "onnx"
    perception = Perception(settings, None, ["sensor", "multimeter", "breadboard", "wires"])
    perception._onnx = (session, ["sensor", "multimeter", "breadboard", "wires"])
    result = perception.detect(Image.new("RGB", (640, 320), "white"))
    assert len(result["objects"]) == 2  # overlapping sensor boxes are suppressed
    assert {d["label"] for d in result["objects"]} == {"sensor", "multimeter"}
    tensor = session.inputs["images"]
    assert tensor.shape == (1, 3, 640, 640) and tensor.dtype == np.float32
    assert tensor[0, 0, 0, 0] == pytest.approx(114 / 255)
    assert all(0 <= coordinate <= 1 for item in result["objects"] for coordinate in item["bbox"])


def test_onnx_wrong_output_explicit(settings):
    settings.vision_provider = "onnx"
    perception = Perception(settings, None, ["sensor"])
    perception._onnx = (Session(np.zeros((1, 100, 5))), ["sensor"])
    with pytest.raises(ProviderError, match="输出"):
        perception.detect(Image.new("RGB", (30, 30)))


def test_onnx_input_size_validation(settings):
    settings.vision_provider = "onnx"
    settings.onnx_size = 320
    perception = Perception(settings, None, ["sensor"])
    perception._onnx = (Session(np.zeros((1, 5, 100))), ["sensor"])
    with pytest.raises(ProviderError, match="尺寸"):
        perception.detect(Image.new("RGB", (30, 30)))


def test_onnx_runtime_file_loading_contract(settings, tmp_path, monkeypatch):
    import onnxruntime as ort
    model = tmp_path / "test.onnx"
    model.write_bytes(b"test-only constructor fixture; not a trained model")
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps(["sensor"]), encoding="utf-8")
    settings.onnx_model, settings.onnx_labels = str(model), str(labels)
    session = Session(np.zeros((1, 5, 1), dtype=np.float32))
    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: session)
    perception = Perception(settings, None, ["sensor"])
    assert perception._load_onnx()[0] is session
    assert perception._load_onnx()[0] is session


def test_unavailable_execution_provider_never_silently_falls_back(settings, tmp_path):
    model, labels = tmp_path / "test.onnx", tmp_path / "labels.json"
    model.write_bytes(b"test")
    labels.write_text('["sensor"]', encoding="utf-8")
    settings.onnx_model, settings.onnx_labels = str(model), str(labels)
    settings.onnx_providers = "ImaginaryNpuProvider"
    with pytest.raises(ProviderError, match="不可用"):
        Perception(settings, None, ["sensor"])._load_onnx()


def test_tesseract_execution_contract(settings, monkeypatch):
    settings.ocr_provider = "tesseract"
    def run(arguments, **kwargs):
        assert arguments[-1] == "tsv" and kwargs["timeout"] == 20
        assert "shell" not in kwargs
        return SimpleNamespace(stdout=b"header\n5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t99\t1.25\n"
                                      b"5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t98\tV\n")
    monkeypatch.setattr("labagent.perception.subprocess.run", run)
    result = Perception(settings, None, []).read_voltage(Image.new("RGB", (100, 100)), [0, 0, 1, 1])
    assert result["value"] == 1.25 and result["confidence"] == .98


def test_tesseract_missing_binary_explicit(settings, monkeypatch):
    settings.ocr_provider = "tesseract"
    def missing(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr("labagent.perception.subprocess.run", missing)
    with pytest.raises(ProviderError, match="未安装"):
        Perception(settings, None, []).read_voltage(Image.new("RGB", (100, 100)), [0, 0, 1, 1])


def test_vlm_reading_converts_mv(settings):
    class Gateway:
        def chat(self, messages, **kwargs):
            assert kwargs["vision"]
            assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
            return {"content": '{"value":750,"unit":"mV","confidence":0.98}'}
    settings.ocr_provider = "vlm"
    result = Perception(settings, Gateway(), []).read_voltage(Image.new("RGB", (100, 100)), [0, 0, 1, 1])
    assert result["value"] == .75 and result["unit"] == "V"


def test_vlm_unreadable_and_invalid_reading(settings):
    class Gateway:
        content = '{"value":null,"unit":"V","confidence":0.1}'
        def chat(self, *a, **k):
            return {"content": self.content}
    settings.ocr_provider = "vlm"
    gateway = Gateway()
    perception = Perception(settings, gateway, [])
    image = Image.new("RGB", (100, 100))
    assert perception.read_voltage(image, [0, 0, 1, 1])["value"] is None
    gateway.content = '{"value":10,"unit":"A","confidence":1}'
    with pytest.raises(ProviderError):
        perception.read_voltage(image, [0, 0, 1, 1])


def test_http_timeout_and_invalid_json():
    settings = Settings(llm_base_url="http://localhost/v1", llm_model="x")
    def timeout(request):
        raise httpx.ReadTimeout("test", request=request)
    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(ProviderError, match="超时"):
            Gateway(settings, client).chat([])
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"not-json"))) as client:
        with pytest.raises(ProviderError, match="JSON"):
            Gateway(settings, client).chat([])


def test_transcription_http_contract():
    seen = []
    def response(request):
        seen.append(request)
        return httpx.Response(200, json={"text": "现在做到哪一步了"})
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        gateway = Gateway(Settings(asr_base_url="http://localhost/v1", asr_model="local-asr"), client)
        assert "哪一步" in gateway.transcribe(b"test-audio", "test.webm", "audio/webm")
    assert seen[0].url.path == "/v1/audio/transcriptions"
