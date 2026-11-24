from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_dir: Path = ROOT / "data"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    access_token: str = ""
    local_only: bool = True
    evidence_ttl_seconds: int = Field(default=300, ge=10, le=86400)
    min_confidence: float = Field(default=0.85, ge=0.5, le=1)
    http_timeout: float = Field(default=45, gt=0, le=300)
    max_tool_rounds: int = Field(default=4, ge=1, le=8)
    agent_max_actions: int = Field(default=12, ge=3, le=50)
    agent_ticket_seconds: int = Field(default=120, ge=15, le=300)
    agent_semantic_equipment: bool = True
    agent_decision_format: str = Field(default="json",pattern="^(json|tools)$")
    agent_max_tokens: int = Field(default=192, ge=96, le=384)
    meter_live_enabled: bool = False
    chat_provider: str = "rules"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    vision_provider: str = "disabled"
    vlm_base_url: str = ""
    vlm_api_key: str = ""
    vlm_model: str = ""
    onnx_model: str = ""
    onnx_labels: str = ""
    onnx_size: int = Field(default=640, ge=32, le=2048)
    onnx_providers: str = "CPUExecutionProvider"
    ocr_provider: str = "disabled"
    ocr_base_url: str = ""
    tesseract_command: str = "tesseract"
    meter_roi: str = ""
    asr_base_url: str = ""
    asr_api_key: str = ""
    asr_model: str = ""

    @field_validator("chat_provider")
    @classmethod
    def chat_choice(cls, value):
        if value not in {"rules", "api"}:
            raise ValueError("CHAT_PROVIDER must be rules or api")
        return value

    @field_validator("vision_provider")
    @classmethod
    def vision_choice(cls, value):
        if value not in {"disabled", "vlm", "onnx"}:
            raise ValueError("VISION_PROVIDER must be disabled, vlm or onnx")
        return value

    @field_validator("ocr_provider")
    @classmethod
    def ocr_choice(cls, value):
        if value not in {"disabled", "tesseract", "vlm", "cix", "mnn"}:
            raise ValueError("OCR_PROVIDER must be disabled, tesseract, vlm, cix or mnn")
        return value

    @classmethod
    def load(cls):
        import os

        values = {**dotenv_values(ROOT / ".env"), **os.environ}
        fields = {}
        for key in cls.model_fields:
            value = values.get("LABAGENT_" + key.upper())
            if value is not None:
                fields[key] = value
        result = cls.model_validate(fields)
        if not result.data_dir.is_absolute():
            result.data_dir = ROOT / result.data_dir
        return result

    def public(self):
        return {
            "chat_provider": self.chat_provider,
            "local_only": self.local_only,
            "llm_configured": bool(self.llm_base_url and self.llm_model),
            "vision_provider": self.vision_provider,
            "ocr_provider": self.ocr_provider,
            "vlm_configured": bool(self.vlm_base_url and self.vlm_model),
            "asr_configured": bool(self.asr_base_url and self.asr_model),
            "access_token_required": bool(self.access_token),
            "evidence_ttl_seconds": self.evidence_ttl_seconds,
            "meter_live_enabled": self.meter_live_enabled,
            "npu_status": "OCR 和语音服务运行状态请分别检查其 /health",
        }
