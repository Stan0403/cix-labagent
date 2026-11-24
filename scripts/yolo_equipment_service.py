"""Local equipment-only YOLO service; no VLM fallback."""
from contextlib import asynccontextmanager
import io
import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from labagent.yolo_equipment import CLASSES, EquipmentDetector

ROOT = Path(__file__).resolve().parent.parent
detector = None


@asynccontextmanager
async def lifespan(_):
    global detector
    model = os.environ.get("LABAGENT_YOLO_MODEL", str(ROOT/"models/equipment-yolo26-v1/equipment_yolo26n_960_heads_int8_bias32_packed.cix"))
    detector = EquipmentDetector(model, int(os.environ.get("LABAGENT_YOLO_SIZE","960")),
                                 os.environ.get("LABAGENT_YOLO_BACKEND","cix"),
                                 float(os.environ.get("LABAGENT_YOLO_CONFIDENCE",".35")))
    try:
        yield
    finally:
        detector.close()
        detector = None


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"ready":detector is not None, "backend":detector.backend if detector else None,
            "input_size":detector.size if detector else None, "classes":list(CLASSES),
            "requests":detector.requests if detector else 0}


@app.post("/inspect")
def inspect(file: UploadFile=File(...), labels: str=Form(...)):
    try:
        items = json.loads(labels)
        if not isinstance(items,dict) or not 1 <= len(items) <= 20 or any(
            not isinstance(k,str) or not isinstance(v,str) or not 0 < len(k) <= 80 or not 0 < len(v) <= 80
            for k,v in items.items()
        ):
            raise ValueError()
        raw = file.file.read(8_000_001)
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,"器材清单无效") from exc
    finally:
        file.file.close()
    if len(raw) > 8_000_000:
        raise HTTPException(413,"图片上限 8 MB")
    try:
        with Image.open(io.BytesIO(raw)) as original:
            if original.format not in {"JPEG","PNG","WEBP"} or original.width*original.height > 20_000_000:
                raise ValueError()
            image = ImageOps.exif_transpose(original).convert("RGB")
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError) as exc:
        raise HTTPException(422,"图片无效") from exc
    if detector is None:
        raise HTTPException(503,"YOLO 器材检测服务未就绪")
    try:
        return detector.inspect(image,items)
    except (RuntimeError,ValueError,OSError) as exc:
        raise HTTPException(503,"YOLO 推理失败，请重试或人工核对") from exc
