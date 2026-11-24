from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from labagent.local_asr import AudioError, ctc_decode, read_cmvn, stack_lfr
from labagent.speech_service import create_speech_app


def test_lfr_left_and_right_padding():
    feats = np.arange(8 * 80, dtype=np.float32).reshape(8, 80)
    out = stack_lfr(feats)
    assert out.shape == (2, 560)
    assert np.array_equal(out[0, :320], np.tile(feats[0], 4))
    assert np.array_equal(out[1, -160:], np.tile(feats[-1], 2))


def test_empty_lfr_is_rejected():
    with pytest.raises(AudioError):
        stack_lfr(np.zeros((0, 80), dtype=np.float32))


def test_cmvn_loading(tmp_path):
    path = tmp_path / "am.mvn"
    path.write_text("<AddShift> 560 560\n<LearnRateCoef> 0 [ " + " ".join(["-1"] * 560)
                    + " ]\n<Rescale> 560 560\n<LearnRateCoef> 0 [ " + " ".join(["2"] * 560) + " ]\n")
    shift, scale = read_cmvn(path)
    assert shift.shape == (560,) and np.all(shift == -1) and np.all(scale == 2)


def test_invalid_cmvn_rejected(tmp_path):
    path = tmp_path / "am.mvn"
    path.write_text("<AddShift> 1 1\n<LearnRateCoef> 0 [ 1 ]\n")
    with pytest.raises(AudioError):
        read_cmvn(path)


def test_ctc_consecutive_and_blank_handling():
    logits = np.zeros((256, 25055), dtype=np.float32)
    for i, token in enumerate([1, 1, 0, 2, 2, 0, 2]):
        logits[i, token] = 10
    class Tokenizer:
        def decode(self, ids):
            assert ids == [1, 2, 2]
            return "<|zh|><|NEUTRAL|>测试试"
    assert ctc_decode(logits, [7], Tokenizer()) == "测试试"


@pytest.mark.parametrize("length", [[-1], [257], [float("nan")], [1, 2]])
def test_invalid_ctc_lengths(length):
    with pytest.raises(AudioError):
        ctc_decode(np.zeros((256, 25055), np.float32), length, None)


class FakeModel:
    closed = False

    def __init__(self, directory):
        self.directory = directory

    def transcribe(self, waveform, language="zh"):
        assert language == "zh"
        return {"text": "现在做到哪一步了", "backend": "test-only"}

    def close(self):
        self.closed = True


def test_asr_http_contract_with_controlled_model(monkeypatch):
    monkeypatch.setattr("labagent.speech_service.decode_audio", lambda raw: np.zeros(16000, np.float32))
    with TestClient(create_speech_app(Path("test-only"), FakeModel)) as client:
        assert client.get("/health").json()["status"] == "ok"
        response = client.post("/v1/audio/transcriptions", data={"model": "sensevoice-small-cix"},
                               files={"file": ("sample.webm", b"test-only-audio", "audio/webm")})
        assert response.status_code == 200 and response.json()["text"] == "现在做到哪一步了"
        assert client.post("/v1/audio/transcriptions", data={"model": "unknown"},
                           files={"file": ("sample.webm", b"test", "audio/webm")}).status_code == 422


def test_failed_npu_model_is_not_reported_ready():
    def failing(_):
        raise RuntimeError("test initialization failure")
    with TestClient(create_speech_app(Path("test-only"), failing)) as client:
        assert client.get("/health").status_code == 503
        response = client.post("/v1/audio/transcriptions", files={"file": ("s.wav", b"test", "audio/wav")})
        assert response.status_code == 503


def test_asr_rejects_nonlocal_host():
    with TestClient(create_speech_app(Path("test-only"), FakeModel)) as client:
        assert client.get("/health", headers={"Host": "external.example"}).status_code == 403


def test_invalid_audio_returns_error(monkeypatch):
    def invalid(_):
        raise AudioError("invalid test audio")
    monkeypatch.setattr("labagent.speech_service.decode_audio", invalid)
    with TestClient(create_speech_app(Path("test-only"), FakeModel)) as client:
        response = client.post("/v1/audio/transcriptions", files={"file": ("s.wav", b"test", "audio/wav")})
        assert response.status_code == 422
