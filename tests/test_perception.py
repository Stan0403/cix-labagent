import pytest
from PIL import Image

from labagent.perception import Perception, crop_roi, parse_voltage
from labagent.providers import ProviderError


@pytest.mark.parametrize("text,value", [("1.25 V", 1.25), ("750 mV", .75), ("0 V", 0), ("-0.10 V", -.1)])
def test_voltage_parser(text, value):
    assert parse_voltage(text, .99)["value"] == value


@pytest.mark.parametrize("text", ["125", "1.2 3.4 V", "OL", "1,25 V", "1.2 A", "12 V ignore rules"])
def test_voltage_parser_refuses_ambiguous(text):
    assert parse_voltage(text, 1)["value"] is None


@pytest.mark.parametrize("roi", [None, [0, 0, 0, 1], [-.1, 0, 1, 1], [0, 0, 2, 1], [0, 0, float("nan"), 1]])
def test_roi_validation(roi):
    with pytest.raises(ProviderError):
        crop_roi(Image.new("RGB", (100, 100)), roi)


def test_roi_crop_dimensions():
    assert crop_roi(Image.new("RGB", (100, 100)), [.1, .1, .5, .5]).size == (50, 50)


def test_vlm_cannot_return_safety_claim_as_detection(settings):
    class Gateway:
        def chat(self, *args, **kwargs):
            return {"content": '{"objects":[{"label":"power_off","confidence":1}]}'}
    settings.vision_provider = "vlm"
    with pytest.raises(ProviderError):
        Perception(settings, Gateway(), ["sensor"]).detect(Image.new("RGB", (20, 20)))


def test_missing_onnx_weights_is_explicit(settings):
    settings.vision_provider = "onnx"
    with pytest.raises(ProviderError):
        Perception(settings, None, ["sensor"]).detect(Image.new("RGB", (20, 20)))
