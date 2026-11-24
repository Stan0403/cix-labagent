"""Local keyboard camera collector; isolated from experiment and model services.

Run on the O6: .venv-app/bin/python scripts/lcd_capture.py --open-browser
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import threading
import uuid
import webbrowser

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from PIL import Image, UnidentifiedImageError
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "labagent/web"
MAX_BYTES = 16_000_000
IDENTIFIER = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
READING = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$")
KINDS = {"unlabeled", "reading", "blank", "unreadable", "overrange"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def atomic_write(path, raw):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class CaptureStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.lock = threading.RLock()

    def batch(self, batch_id):
        if not IDENTIFIER.fullmatch(batch_id):
            raise HTTPException(422, "批次编号无效")
        path = self.directory / batch_id
        if path.is_symlink() or not (path / "batch.json").is_file():
            raise HTTPException(404, "采集批次不存在，请新建批次")
        return path

    def records(self, batch_id):
        path = self.batch(batch_id)
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((path / "records").glob("*.json"))]

    def create(self, name):
        with self.lock:
            batch_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
            path = self.directory / batch_id
            (path / "images").mkdir(parents=True, exist_ok=False)
            (path / "records").mkdir()
            metadata = {
                "batch_id": batch_id,
                "name": name.strip()[:100],
                "created_at": now(),
                "purpose": "LCD OCR training collection",
                "split": "unassigned",
            }
            atomic_write(path / "batch.json", json.dumps(metadata, ensure_ascii=False, indent=2).encode())
            return {**metadata, "count": 0, "directory": str(path)}

    def info(self, batch_id):
        with self.lock:
            path = self.batch(batch_id)
            metadata = json.loads((path / "batch.json").read_text(encoding="utf-8"))
            return {**metadata, "count": len(self.records(batch_id)), "directory": str(path)}

    def save(self, batch_id, capture_id, raw, kind, text, note, camera):
        if not re.fullmatch(r"[a-f0-9]{32}", capture_id):
            raise HTTPException(422, "照片编号无效")
        if kind not in KINDS:
            raise HTTPException(422, "样本类型无效")
        text = text.strip()
        if kind == "reading" and (len(text) > 25 or not READING.fullmatch(text)):
            raise HTTPException(422, "请按屏幕原样输入数字、负号和小数点；无法看清时选择看不清")
        if kind == "overrange" and not re.fullmatch(r"[a-zA-Z0-9.+-]{1,16}", text):
            raise HTTPException(422, "请填写超量程时实际显示的字符，如 OL 或 1")
        label = text if kind in {"reading", "overrange"} else "" if kind == "blank" else None
        if not raw or len(raw) > MAX_BYTES:
            raise HTTPException(413, "照片为空或超过 16 MB")
        try:
            with Image.open(BytesIO(raw)) as image:
                if image.format != "JPEG" or image.width * image.height > 25_000_000:
                    raise HTTPException(422, "请提交摄像头 JPEG 画面，最多 2500 万像素")
                image.load()
                width, height = image.size
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise HTTPException(422, "照片损坏，请重新拍摄") from exc
        digest = hashlib.sha256(raw).hexdigest()
        with self.lock:
            path = self.batch(batch_id)
            record_path = path / "records" / f"{capture_id}.json"
            if record_path.exists():
                record = json.loads(record_path.read_text(encoding="utf-8"))
                if record["sha256"] != digest or record["kind"] != kind or record["label"] != label:
                    raise HTTPException(409, "同一照片编号已保存了不同内容，请重新拍照")
            else:
                record = {
                    "capture_id": capture_id,
                    "batch_id": batch_id,
                    "group": batch_id,
                    "path": f"images/{capture_id}.jpg",
                    "sha256": digest,
                    "width": width,
                    "height": height,
                    "saved_at": now(),
                    "kind": kind,
                    "label": label,
                    "note": note[:500],
                    "camera": camera[:200],
                    "label_source": "user_entered" if label is not None else "not_labeled",
                    "review_status": "pending",
                    "split": "unassigned",
                    "origin": "camera",
                    "overlay_saved": False,
                }
                atomic_write(path / record["path"], raw)
                atomic_write(record_path, json.dumps(record, ensure_ascii=False, indent=2).encode())
            return {
                "filename": record["path"],
                "record": record,
                "count": len(self.records(batch_id)),
                "directory": str(path),
            }


def create_app(directory=None):
    app = FastAPI(title="O6 液晶数据采集")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    store = CaptureStore(directory or ROOT / "data/ocr-capture")
    app.state.capture_store = store

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method == "POST" and origin and origin != str(request.base_url).rstrip("/"):
            return PlainTextResponse("请从本机采集页面拍照", status_code=403)
        return await call_next(request)

    @app.get("/")
    def page():
        return FileResponse(WEB / "lcd-capture.html", headers={"Cache-Control": "no-store"})

    @app.get("/static/{name}")
    def asset(name: str):
        if name not in {"lcd-capture.js", "lcd-capture.css"}:
            raise HTTPException(404)
        return FileResponse(WEB / name, headers={"Cache-Control": "no-store"})

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "lcd-keyboard-capture", "directory": str(store.directory)}

    @app.post("/api/batches", status_code=201)
    def create_batch(name: str = Form(default="")):
        return store.create(name)

    @app.get("/api/batches/{batch_id}")
    def batch_info(batch_id: str):
        return store.info(batch_id)

    @app.get("/api/batches/{batch_id}/manifest.jsonl")
    def manifest(batch_id: str):
        with store.lock:
            body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in store.records(batch_id))
        return PlainTextResponse(
            body, headers={"Content-Disposition": 'attachment; filename="manifest.jsonl"'}
        )

    @app.post("/api/batches/{batch_id}/photos", status_code=201)
    async def save(
        batch_id: str,
        capture_id: str = Form(),
        kind: str = Form(default="unlabeled"),
        label: str = Form(default=""),
        note: str = Form(default=""),
        camera: str = Form(default=""),
        file: UploadFile = File(),
    ):
        try:
            raw = await file.read(MAX_BYTES + 1)
        finally:
            await file.close()
        from starlette.concurrency import run_in_threadpool

        return await run_in_threadpool(store.save, batch_id, capture_id, raw, kind, label, note, camera)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--output", type=Path, default=ROOT / "data/ocr-capture")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if args.open_browser:
        timer = threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}"))
        timer.daemon = True
        timer.start()
    import uvicorn

    uvicorn.run(create_app(args.output), host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
