"""Loopback-only ASR service compatible with the project's audio/transcriptions adapter."""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from .config import ROOT
from .local_asr import AudioError, SenseVoiceCIX, decode_audio


def create_speech_app(directory=None, model_factory=SenseVoiceCIX):
    directory = Path(directory or os.environ.get("LABAGENT_LOCAL_ASR_DIR", ROOT / "models/sensevoice-cix"))

    @asynccontextmanager
    async def lifespan(app):
        app.state.model = None
        app.state.error = ""
        try:
            app.state.model = model_factory(directory)
        except Exception as exc:
            app.state.error = type(exc).__name__ + ": " + str(exc)
            logging.exception("Local ASR model initialization failed")
        yield
        if app.state.model is not None:
            app.state.model.close()

    app = FastAPI(title="LabAgent Local ASR", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return JSONResponse({"detail": "仅允许本机访问语音服务"}, status_code=403)
        if request.method == "POST":
            length = request.headers.get("content-length", "")
            if not length.isdecimal() or int(length) > 17_000_000:
                return JSONResponse({"detail": "音频请求过大或没有长度"}, status_code=413)
        return await call_next(request)

    @app.exception_handler(AudioError)
    async def audio_error(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/health")
    def health(request: Request):
        ready = request.app.state.model is not None
        return JSONResponse({"status": "ok" if ready else "unavailable", "model": "sensevoice-small-cix",
                             "backend": getattr(request.app.state.model, "backend", "unspecified") if ready else None,
                             "error": request.app.state.error}, status_code=200 if ready else 503)

    @app.post("/v1/audio/transcriptions")
    def transcribe(request: Request, file: Annotated[UploadFile, File()],
                   model: Annotated[str, Form()] = "sensevoice-small-cix",
                   language: Annotated[str, Form()] = "zh"):
        if request.app.state.model is None:
            return JSONResponse({"detail": "本地语音模型不可用，请检查 ASR 服务日志"}, status_code=503)
        if model != "sensevoice-small-cix":
            raise AudioError("模型名应为 sensevoice-small-cix")
        raw = file.file.read(16_000_001)
        file.file.close()
        if len(raw) > 16_000_000:
            return JSONResponse({"detail": "音频文件上限 16 MB"}, status_code=413)
        waveform = decode_audio(raw)
        return request.app.state.model.transcribe(waveform, language=language)

    return app


def main():
    port = int(os.environ.get("LABAGENT_LOCAL_ASR_PORT", "8010"))
    uvicorn.run(create_speech_app(), host="127.0.0.1", port=port, workers=1)


if __name__ == "__main__":
    main()
