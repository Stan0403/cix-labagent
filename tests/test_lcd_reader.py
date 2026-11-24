"""Readout routing and punctuation regressions; real OCR weights are tested separately."""

import importlib.util
import io
import json
from pathlib import Path

from PIL import Image
import pytest
from starlette.datastructures import UploadFile

from labagent import lcd_reader
from labagent.readings import parse_candidate

FIXTURES = Path(__file__).parent / "fixtures/lcd"


def image(name="reading_1_353.png"):
    return Image.open(FIXTURES / name).convert("RGB")


def upload(name="reading_1_353.png"):
    return UploadFile(io.BytesIO((FIXTURES / name).read_bytes()), filename="display.png")


def test_visible_leading_decimal_rejects_model_shift():
    evidence = lcd_reader.punctuation_evidence(image("leading_decimal_419.png"))
    assert evidence["certain"] and evidence["decimal_after"] == 0
    assert lcd_reader.check_numeric(".419", evidence)[0]
    assert lcd_reader.check_numeric("41.9", evidence) == (False, "decimal_position_mismatch")


def test_negative_source_pixels_reject_missing_minus():
    evidence = lcd_reader.punctuation_evidence(image("negative_zero.png"))
    assert evidence["certain"] and evidence["minus"]
    assert lcd_reader.check_numeric("-0.00", evidence)[0]
    assert lcd_reader.check_numeric("0.00", evidence) == (False, "sign_mismatch")


def test_no_multiple_decimal_or_single_overrange_fast_accept(monkeypatch):
    reader = object.__new__(lcd_reader.LcdReader)
    for text in ["1.3.53", "1", "OL", "皖353"]:
        monkeypatch.setattr(reader, "predict", lambda *a, **kw: (text, 1.0, 1.0))
        assert not reader.read(image())["accepted"]


@pytest.mark.parametrize("text,value", [(".419", 0.419), ("-.012", -0.012), ("1.353", 1.353)])
def test_leading_decimal_is_parsed_without_moving_it(text, value):
    assert parse_candidate(json.dumps({"reading": text, "unit": None}), "dc_20v")["value"] == pytest.approx(
        value
    )


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.delenv("LABAGENT_LCD_OCR_MODEL", raising=False)
    path = Path(__file__).parents[1] / "scripts/qwen3vl_gguf_service.py"
    spec = importlib.util.spec_from_file_location("lcd_adapter_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeReader:
    model_sha256 = "fixture-model"

    def __init__(self, text, accepted, evidence=None):
        self.result = {
            "text": text,
            "accepted": accepted,
            "confidence": 0.99,
            "backend": lcd_reader.BACKEND,
            "reason": "fixture",
            "evidence": evidence or {},
        }

    def read(self, image):
        return self.result


def test_accepted_ocr_bypasses_vlm(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "LCD_READER", FakeReader("1.353", True))
    monkeypatch.setattr(adapter, "infer", lambda *a, **kw: pytest.fail("Accepted OCR must not invoke VLM"))
    result = adapter.recognize(upload())
    assert json.loads(result["text"])["reading"] == "1.353"
    assert result["backend"] == lcd_reader.BACKEND and not result["fallback_used"]


def test_empty_ocr_returns_unknown_not_zero(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "LCD_READER", FakeReader("", False))
    monkeypatch.setattr(adapter, "infer", lambda *a, **kw: pytest.fail("No numeric OCR result"))
    assert json.loads(adapter.recognize(upload())["text"])["reading"] is None


@pytest.mark.parametrize(
    "fallback,expected", [("41.9", None), (".419", 0.419), (",419", 0.419), ("0.419", 0.419)]
)
def test_vlm_fallback_cannot_override_visible_decimal(adapter, monkeypatch, fallback, expected):
    evidence = lcd_reader.punctuation_evidence(image("leading_decimal_419.png"))
    monkeypatch.setattr(adapter, "LCD_READER", FakeReader("41.9", False, evidence))
    monkeypatch.setattr(adapter, "infer", lambda *a, **kw: json.dumps({"reading": fallback, "unit": None}))
    result = adapter.recognize(upload("leading_decimal_419.png"))
    assert result["fallback_used"] and result["backend"] == adapter.BACKEND
    assert parse_candidate(result["text"], "dc_20v")["value"] == expected


def test_long_malformed_fallback_never_leaks_a_number_into_candidate(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "LCD_READER", FakeReader("41.9", False))
    monkeypatch.setattr(adapter, "infer", lambda *a, **kw: "reading: 41.9 " + ("x" * 5000))
    result = adapter.recognize(upload())
    assert len(result["text"]) < 500
    assert parse_candidate(result["text"], "dc_200v")["value"] is None


def test_missing_decimal_in_vlm_cannot_replace_valid_ocr_without_pixel_proof(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "LCD_READER", FakeReader("1.25", False, {"certain": False}))
    monkeypatch.setattr(adapter, "infer", lambda *a, **kw: '{"reading":"125","unit":null}')
    result = adapter.recognize(upload())
    assert parse_candidate(result["text"], "dc_200v")["value"] is None


def test_fallback_missing_first_digit_is_rejected_even_when_geometry_agrees(adapter, monkeypatch):
    # Real board regression: the slim leading 1 was missed by both the VLM
    # and connected components. Punctuation agreement cannot verify the digits.
    evidence = {"certain": True, "digit_count": 2, "minus": False, "decimal_after": 0}
    monkeypatch.setattr(adapter, "LCD_READER", FakeReader("1.24", False, evidence))
    monkeypatch.setattr(adapter, "infer", lambda *a, **kw: '{"reading":".24","unit":null}')
    result = adapter.recognize(upload())
    assert parse_candidate(result["text"], "dc_20v")["value"] is None
    assert json.loads(result["text"])["reason"] == "reader_disagreement_on_digits"


def test_comma_normalization_needs_positive_pixel_evidence(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "LCD_READER", FakeReader("1.353", False, {"certain": False}))
    monkeypatch.setattr(adapter, "infer", lambda *a, **kw: '{"reading":"1,353","unit":null}')
    assert parse_candidate(adapter.recognize(upload())["text"], "dc_200v")["value"] is None


def test_fallback_overrange_is_not_one_volt(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "LCD_READER", FakeReader("1", False))
    monkeypatch.setattr(adapter, "infer", lambda *a, **kw: '{"reading":"1","unit":null}')
    assert parse_candidate(adapter.recognize(upload())["text"], "dc_20v")["value"] is None


def test_backend_health_exposes_reading_model(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "LCD_READER", FakeReader("1.34", True))
    assert adapter.reading_health()["reading_backend"] == lcd_reader.BACKEND
    assert adapter.reading_health()["reading_model_sha256"] == "fixture-model"
