"""LabAgent-compatible image tools backed by a local llama.cpp VLM server."""
import base64
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from labagent.readings import parse_display_box, parse_vlm_detections
from PIL import Image, ImageOps, UnidentifiedImageError

BACKEND = "qwen3-vl-4b-q4_k_m-llamacpp-cpu"
SERVER = os.environ.get("LABAGENT_VLM_SERVER", "http://127.0.0.1:8032").rstrip("/")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LOCK = threading.Lock()
app = FastAPI()


@app.get("/health")
def health():
    try:
        with OPENER.open(SERVER + "/health", timeout=3) as response:
            ready = response.status == 200
    except (OSError, urllib.error.URLError):
        ready = False
    return {"ready": ready, "busy": LOCK.locked(), "backend": BACKEND}


def decode(raw):
    if len(raw) > 8_000_000:
        raise HTTPException(413, "图片过大")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"} or source.width * source.height > 20_000_000:
                raise ValueError()
            return ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(400, "图片无效") from exc


def read_upload(file):
    try:
        return decode(file.file.read(8_000_001))
    finally:
        file.file.close()


def infer(image, instruction, tokens=256, max_side=896, deadline=None):
    if not LOCK.acquire(blocking=False):
        raise HTTPException(429, "视觉模型忙，请等待上一项观察完成")
    try:
        frame = image.copy()
        frame.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        frame.save(buffer, format="JPEG", quality=95)
        image_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        payload = {
            "model": "qwen3-vl-4b-local", "temperature": 0, "max_tokens": tokens,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": instruction},
            ]}],
        }
        request = urllib.request.Request(SERVER + "/v1/chat/completions",
                                         data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        remaining = 220 if deadline is None else deadline - time.monotonic()
        if remaining <= 0:
            raise HTTPException(503, "视觉复核超时，请重试或人工核对")
        with OPENER.open(request, timeout=min(220, remaining)) as response:
            result = json.load(response)
        if result["choices"][0].get("finish_reason") == "length":
            raise HTTPException(503, "视觉回答不完整，请重试或人工核对")
        text = result["choices"][0]["message"].get("content")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(503, "视觉模型未返回有效文本")
        return text.strip()[:6000]
    except HTTPException:
        raise
    except (OSError, urllib.error.URLError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(503, "本地视觉推理失败或超时，请重试或人工核对") from exc
    finally:
        LOCK.release()


def verify_probes(image, parsed, requested, deadline):
    """Reject connector/wire hypotheses with a separate, zoomed visual check."""
    raw_checks = []
    for key, name in requested.items():
        lower = name.lower()
        if "probe" not in lower and "表笔" not in name:
            continue
        candidates = [box for box in parsed["boxes"] if box["label"] == key]
        accepted = []
        for box in candidates:
            x, y, width, height = box["bbox"]
            left, top = max(0, int(x * image.width) - 3), max(0, int(y * image.height) - 3)
            right = min(image.width, int((x + width) * image.width) + 3)
            bottom = min(image.height, int((y + height) * image.height) + 3)
            if right - left < 6 or bottom - top < 6:
                continue
            text = infer(image.crop((left, top, right, bottom)),
                         'Classify the central object from its visible shape. A handheld multimeter test probe '
                         'has a thin exposed pointed metal needle and an elongated insulated handle. '
                         'A banana connector inserted into a socket, a wire, or the meter itself is NOT '
                         'a handheld pointed probe. Return ONLY JSON: '
                         '{"handheld_probe":true|false|null,"color":"red"|"black"|"other"|null}. '
                         'Do not assume the expected object is present.', tokens=96, max_side=384, deadline=deadline)
            raw_checks.append({"label": key, "text": text})
            content = text.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                verdict = json.loads(content)
            except ValueError:
                continue
            if not isinstance(verdict, dict) or verdict.get("handheld_probe") is not True:
                continue
            expected = "red" if "red" in lower or "红" in name else "black" if "black" in lower or "黑" in name else None
            if expected and verdict.get("color") != expected:
                continue
            accepted.append(box)
        parsed["boxes"] = [box for box in parsed["boxes"] if box["label"] != key] + accepted
        if parsed["present"][key] is True and not accepted:
            # Rejecting a proposed location does not prove absence elsewhere.
            parsed["present"][key] = None
    return raw_checks


@app.post("/inspect")
def inspect(file: UploadFile = File(...), labels: str = Form(...)):
    try:
        items = json.loads(labels)
        if (not isinstance(items, dict) or not 1 <= len(items) <= 20
                or any(not isinstance(k, str) or not isinstance(v, str)
                       or len(k) > 80 or len(v) > 80 for k, v in items.items())):
            raise ValueError()
    except ValueError as exc:
        file.file.close()
        raise HTTPException(422, "器材清单无效") from exc
    image = read_upload(file)
    requested, groups = {}, {}
    for key, name in items.items():
        lower = name.lower()
        paired = ("红" in name and "黑" in name and "表笔" in name) or (
            "red" in lower and "black" in lower and "probe" in lower)
        if not paired:
            requested[key] = name
            continue
        index = len(groups)
        parts = [f"__labagent_{color}_{index}" for color in ("red", "black")]
        while any(part in items or part in requested for part in parts):
            parts = [part + "_" for part in parts]
        groups[key] = parts
        requested[parts[0]] = "red handheld multimeter test probe, metal tip and handle only"
        requested[parts[1]] = "black handheld multimeter test probe, metal tip and handle only"
    prompt = (
        'Inspect only what is actually visible in this image. Requested equipment (key: name): '
        + json.dumps(requested, ensure_ascii=False)
        + '. Return ONLY a JSON array, one entry per requested item. For a clearly visible item: '
        '{"label":"key","bbox_2d":[x1,y1,x2,y2]}. Coordinates MUST be normalized to 0-1000 '
        'relative to the entire image, with top-left origin. Box the actual object, not unrelated cables. '
        'For a test probe, box the handheld pointed metal tip and its handle; exclude trailing wires '
        'and banana plugs inserted into the meter. If the requested label groups red and black probes, '
        'box both handheld probes together. '
        'For a clearly absent item: {"label":"key","present":false}. '
        'For an uncertain item: {"label":"key","present":null}. Do not guess.'
    )
    deadline = time.monotonic() + 220
    text = infer(image, prompt, tokens=512, deadline=deadline)
    # Qwen3-VL uses 0-1000 boxes; the shared parser returns normalized xywh.
    parsed = parse_vlm_detections(text, requested, 1000, 1000)
    checks = verify_probes(image, parsed, requested, deadline)
    if checks:
        text += "\n--- probe crop verification ---\n" + json.dumps(checks, ensure_ascii=False)
    present = {key: parsed["present"][key] for key in items if key not in groups}
    boxes = [box for box in parsed["boxes"] if box["label"] in present]
    for key, parts in groups.items():
        states = [parsed["present"][part] for part in parts]
        present[key] = True if all(s is True for s in states) else False if any(s is False for s in states) else None
        if present[key] is True:
            boxes.extend({"label": key, "bbox": box["bbox"]} for box in parsed["boxes"] if box["label"] in parts)
    return {"present": present, "boxes": boxes, "coordinate_space": "normalized_xywh", "raw_text": text,
            "backend": BACKEND, "requires_confirmation": True}


@app.post("/recognize")
def recognize(file: UploadFile = File(...)):
    text = infer(read_upload(file),
                 'Read only the large LCD digits in this image. Return ONLY JSON: '
                 '{"reading":"exact digit string including sign and decimal point","unit":null}. '
                 'If unreadable, use {"reading":null,"unit":null}. '
                 'Ignore model numbers, dial markings and printed range numbers. '
                 'Do not infer missing digits or units.', tokens=96, max_side=1120)
    return {"text": text, "confidence": 0, "backend": BACKEND}


@app.post("/locate_display")
def locate_display(file: UploadFile = File(...)):
    image = read_upload(file)
    try:
        from labagent.vision_regions import lcd_region
        roi = lcd_region(image)
    except (ImportError, OSError, ValueError):
        roi = None
    if roi:
        return {"roi": roi, "backend": "opencv-lcd-region",
                "raw_text": "自动检测到显示屏区域，随后从原图裁剪读取"}
    text = infer(image, 'Locate the multimeter LCD screen only. Return ONLY [left,top,right,bottom] '
                 'normalized to 0-1000 relative to the whole image. Include the LCD digits and sign, '
                 'exclude the dial. If uncertain return null.', tokens=64)
    return {"roi": parse_display_box(text), "raw_text": text, "backend": BACKEND}
