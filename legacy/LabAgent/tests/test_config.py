import pytest
from pydantic import ValidationError

from labagent.config import ROOT, Settings


def test_loads_blank_keys_and_relative_data_path(monkeypatch):
    monkeypatch.setenv("LABAGENT_DATA_DIR", "test-results/config-test")
    monkeypatch.setenv("LABAGENT_LLM_API_KEY", "")
    result = Settings.load()
    assert result.llm_api_key == "" and result.data_dir == ROOT / "test-results/config-test"


@pytest.mark.parametrize("field", ["chat_provider", "vision_provider", "ocr_provider"])
def test_invalid_provider_configuration_rejected(field):
    with pytest.raises(ValidationError):
        Settings(**{field: "imaginary"})


def test_secrets_not_returned_by_public_configuration():
    settings = Settings(llm_api_key="llm-secret", vlm_api_key="vlm-secret", asr_api_key="asr-secret")
    assert "secret" not in str(settings.public())
