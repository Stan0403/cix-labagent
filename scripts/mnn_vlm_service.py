"""Local, human-verified meter reading preview using the CIX-listed MNN VLM."""
import io
import json
import re
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import MNN.cv as cv
import MNN.llm as llm
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from labagent.readings import parse_display_box, parse_vlm_detections

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "models" / "qwen25vl-mnn" / "config.json"
MODEL_LOCK = threading.Lock()
STATE = {}


@asynccontextmanager
async def lifespan(app):
    model = llm.create(str(MODEL))
    # This MNN export needs Qwen ChatML; the default template in MNN 3.6
    # omits its role markers and produces an empty multimodal answer.
    model.set_config({"max_new_tokens": 96, "reuse_kv": False, "use_template": False})
    model.load()
    STATE["model"] = model
    yield
    STATE.clear()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"ready": "model" in STATE, "busy": MODEL_LOCK.locked(), "backend": "Qwen2.5-VL-3B MNN CPU"}


def decode_image(data):
    if len(data) > 8_000_000:
        raise HTTPException(413, "图片过大")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"} or source.width * source.height > 20_000_000:
                raise ValueError()
            return ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(400, "图片无效") from exc


def infer(file, instruction, max_tokens, max_side=768):
    if isinstance(file, bytes):
        data = file
    else:
        data = file.file.read(8_000_001)
        file.file.close()
    if len(data) > 8_000_000:
        raise HTTPException(413, "图片过大")
    try:
        with Image.open(io.BytesIO(data)) as original:
            if original.format not in {"JPEG", "PNG", "WEBP"} or original.width * original.height > 20_000_000:
                raise ValueError()
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((max_side, max_side))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(400, "图片无效") from exc
    if not MODEL_LOCK.acquire(blocking=False):
        raise HTTPException(429, "视觉模型忙，请等待上一项观察完成")
    try:
        if "model" not in STATE:
            raise HTTPException(503, "视觉模型尚未就绪")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "display.png"
            image.save(path)
            model = STATE["model"]
            model.reset()
            model.set_config({"max_new_tokens": max_tokens})
            var = cv.imread(str(path))
            prompt = {"text": "<|im_start|>user\n<img>image_0</img>" + instruction +
                      "<|im_end|>\n<|im_start|>assistant\n",
                      "images": [{"data": var, "height": image.height, "width": image.width}]}
            text = model.response(prompt, False)
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(503, "视觉模型未返回有效文本")
        return text.strip()[:2000]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "视觉推理失败，请重试或人工核对") from exc
    finally:
        MODEL_LOCK.release()


@app.post("/recognize")
def recognize(file: UploadFile = File(...)):
    text = infer(file, 'Read the large LCD digits. Return ONLY JSON with keys "reading" and "unit". '
                 'reading is the exact digit string including any visible sign and decimal point; unit is null if not visible. '
                 'If unreadable, reading is null. Do not guess missing digits. No explanation.', 64)
    return {"text": text, "confidence": 0, "backend": "qwen25-vl-mnn-cpu"}


@app.post("/inspect")
def inspect(file: UploadFile = File(...), labels: str = Form(...)):
    try:
        items = json.loads(labels)
        if not isinstance(items, dict) or not 1 <= len(items) <= 20 or any(not isinstance(v, str) or len(v) > 80 for v in items.values()):
            raise ValueError()
    except ValueError as exc:
        raise HTTPException(422, "器材清单无效") from exc
    raw = file.file.read(8_000_001)
    file.file.close()
    image = decode_image(raw)
    image.thumbnail((1120, 1120))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    instruction = (
        'Identify the visible laboratory equipment and give bounding boxes in JSON format. '
        f'Image size is {image.width} x {image.height} pixels. '
        'Format: [{"label":"key","bbox_2d":[x1,y1,x2,y2]}]. '
        'Use pixel coordinates. Omit objects you cannot see. Requested equipment: '
        + json.dumps(items, ensure_ascii=False)
    )
    text = infer(buffer.getvalue(), instruction, 512, max_side=1120)
    normalized = parse_vlm_detections(text, items, image.width, image.height)
    raw_text = [text]
    # Recognition and localization are separate judgments: a visible name can be
    # recognized even when the model cannot supply a usable rectangle.
    missing_boxes = [key for key, value in normalized["present"].items()
                     if value is True and not any(b["label"] == key for b in normalized["boxes"])]
    if missing_boxes:
        prompt = ('Give the bounding box of each requested object in this image. '
                  f'Image width {image.width}, height {image.height} pixels. '
                  'Return JSON only: [{"label":"key","bbox_2d":[x1,y1,x2,y2]}]. Objects: '
                  + json.dumps({key: items[key] for key in missing_boxes}, ensure_ascii=False))
        located_text = infer(buffer.getvalue(), prompt, 384, max_side=1120)
        raw_text.append(located_text)
        located = parse_vlm_detections(located_text, items, image.width, image.height)
        normalized["boxes"].extend(located["boxes"])
        for key, value in located["present"].items():
            if value is True:
                normalized["present"][key] = True
    text = "\n--- localization ---\n".join(raw_text)
    return {**normalized, "coordinate_space": "normalized_xywh", "raw_text": text,
            "backend": "qwen25-vl-mnn-cpu-direct", "requires_confirmation": True}



@app.post("/locate_display")
def locate_display(file: UploadFile = File(...)):
    raw=file.file.read(8_000_001);file.file.close()
    if len(raw)>8_000_000:
        raise HTTPException(413,"图片过大")
    try:
        image = decode_image(raw)
        from labagent.vision_regions import lcd_region
        roi=lcd_region(image)
        if roi:
            return {"roi":roi,"backend":"opencv-lcd-region","raw_text":"自动检测到显示屏区域，随后从原图裁剪读取"}
    except (ImportError,OSError,ValueError):
        pass
    text = infer(raw, 'Locate the multimeter LCD screen only. Return only [left,top,right,bottom], '
                 'coordinates normalized 0-1000, top-left origin. Include digits and sign, exclude the dial. '
                 'If unclear return null.', 48, max_side=560)
    roi = parse_display_box(text)
    return {"roi": roi, "raw_text": text, "backend": "qwen25-vl-mnn-cpu"}
