"""LabAgent-compatible image tools backed by a local llama.cpp VLM server."""
import base64
from decimal import Decimal
import io
import json
import logging
import os
from pathlib import Path
import threading
import time
import urllib.error
from urllib.parse import urlsplit
import urllib.request

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from labagent.readings import parse_display_box, parse_vlm_detections
from PIL import Image, ImageOps, UnidentifiedImageError

BACKEND = os.environ.get("LABAGENT_VLM_BACKEND", "qwen3-vl-4b-q4_k_m-llamacpp-cpu")
SERVER = os.environ.get("LABAGENT_VLM_SERVER", "http://127.0.0.1:8032").rstrip("/")
METER_SERVER = os.environ.get("LABAGENT_METER_VLM_SERVER", "").rstrip("/")
if METER_SERVER:
    meter_url = urlsplit(METER_SERVER)
    if (meter_url.scheme != "http" or meter_url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or meter_url.username or meter_url.password or meter_url.path or meter_url.query or meter_url.fragment):
        raise ValueError("Meter inference must use a local HTTP server root")
METER_LOCK = threading.Lock()
EQUIPMENT_SERVER = os.environ.get("LABAGENT_EQUIPMENT_VLM_SERVER", "").rstrip("/")
if EQUIPMENT_SERVER:
    equipment_url = urlsplit(EQUIPMENT_SERVER)
    if (equipment_url.scheme != "http" or equipment_url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or equipment_url.username or equipment_url.password or equipment_url.path or equipment_url.query
            or equipment_url.fragment):
        raise ValueError("Equipment inference must use a local HTTP server root")
EQUIPMENT_BACKEND = "qwen3-vl-4b-cix-npu-vision-cpu-text-320"
NPU_STATUS = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "labagent-npu-vision.json" if hasattr(os, "getuid") else None
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LOCK = threading.Lock()
app = FastAPI()
LCD_READER = None
LCD_ERROR = ""
LCD_MODEL = os.environ.get("LABAGENT_LCD_OCR_MODEL", "")
if LCD_MODEL:
    try:
        from labagent.lcd_reader import LcdReader
        LCD_READER = LcdReader(LCD_MODEL, os.environ.get("LABAGENT_LCD_OCR_DICT",
            str(Path(LCD_MODEL).with_name("ppocr_keys_v1.txt"))),
            threads=int(os.environ.get("LABAGENT_LCD_OCR_THREADS", "4")))
        expected_sha = os.environ.get("LABAGENT_LCD_OCR_SHA256", "")
        if expected_sha and LCD_READER.model_sha256 != expected_sha:
            raise ValueError("LCD model SHA-256 mismatch")
    except Exception as exc:
        LCD_READER = None
        LCD_ERROR = str(exc)[:300]
        logging.getLogger(__name__).exception("LCD OCR initialization failed; VLM fallback remains available")


def reading_health():
    return {"reading_backend": "ppocr-lcd-v3-user81-onnxruntime-cpu" if LCD_READER else BACKEND,
            "reading_ready": LCD_READER is not None if LCD_MODEL else True,
            "reading_model_sha256": LCD_READER.model_sha256 if LCD_READER else None,
            "reading_error": LCD_ERROR, "reading_fallback": BACKEND,
            "meter_live_configured": bool(METER_SERVER),
            "meter_live_backend": "qwen3-vl-cix-npu-vision-cpu-text-320-closeup-v1" if METER_SERVER else None,
            "meter_live_detail_mode": "separate-dial-terminals-v1" if METER_SERVER else None}

YOLO_SERVER = os.environ.get("LABAGENT_EQUIPMENT_YOLO_SERVER", "").rstrip("/")
if YOLO_SERVER:
    yolo_url = urlsplit(YOLO_SERVER)
    if (yolo_url.scheme != "http" or yolo_url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or yolo_url.username or yolo_url.password or yolo_url.path or yolo_url.query or yolo_url.fragment):
        raise ValueError("Equipment YOLO must use a local HTTP server root")


def yolo_health():
    try:
        with OPENER.open(YOLO_SERVER + "/health", timeout=3) as response:
            return json.load(response)
    except (OSError, urllib.error.URLError, ValueError):
        return {"ready": False, "backend": "yolo26n-unavailable"}


def inspect_yolo(image, items, timeout=15):
    import secrets
    boundary = "labagent-yolo-" + secrets.token_hex(12)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    body = (("--" + boundary + "\r\nContent-Disposition: form-data; name=\"labels\"\r\n\r\n").encode()
            + json.dumps(items, ensure_ascii=False).encode()
            + ("\r\n--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"scene.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n").encode()
            + buffer.getvalue() + ("\r\n--" + boundary + "--\r\n").encode())
    request = urllib.request.Request(YOLO_SERVER + "/inspect", data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with OPENER.open(request, timeout=timeout) as response:
            result = json.load(response)
        if not isinstance(result, dict) or not isinstance(result.get("present"), dict):
            raise ValueError("Invalid equipment response")
        return result
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise HTTPException(503, "YOLO 器材检测不可用，请稍后重试或人工核对") from exc



@app.get("/health")
def health():
    def available(server):
        try:
            with OPENER.open(server + "/health", timeout=3) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    ready = available(SERVER)
    if YOLO_SERVER:
        equipment = yolo_health()
        return {"ready": ready and equipment.get("ready") is True, "busy": LOCK.locked(),
                "backend": BACKEND + "; equipment=" + equipment.get("backend", "yolo26n-unavailable"),
                "default_backend": BACKEND, "equipment_backend": equipment.get("backend"),
                "equipment_ready": equipment.get("ready") is True, **reading_health()}
    if not EQUIPMENT_SERVER:
        return {"ready": ready, "busy": LOCK.locked(), "backend": BACKEND, **reading_health()}
    try:
        state = json.loads(NPU_STATUS.read_text())
        worker_ready = state.get("ready") is True and Path(state["socket"]).is_socket()
        os.kill(int(state["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        worker_ready = False
    equipment_ready = worker_ready and available(EQUIPMENT_SERVER)
    return {"ready": ready and equipment_ready, "busy": LOCK.locked(),
            "backend": BACKEND + "; equipment=" + EQUIPMENT_BACKEND,
            "default_backend": BACKEND, "equipment_backend": EQUIPMENT_BACKEND,
            "equipment_ready": equipment_ready, **reading_health()}


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


def infer(image, instruction, tokens=256, max_side=896, deadline=None, server=None, fixed_grid=False):
    if not LOCK.acquire(blocking=False):
        raise HTTPException(429, "视觉模型忙，请等待上一项观察完成")
    try:
        if fixed_grid:
            frame = ImageOps.pad(image, (320, 320), method=Image.Resampling.BICUBIC, color=(128, 128, 128))
        else:
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
        request = urllib.request.Request((server or SERVER) + "/v1/chat/completions",
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


def infer_equipment(image, instruction, **kwargs):
    if EQUIPMENT_SERVER:
        return infer(image, instruction, server=EQUIPMENT_SERVER, fixed_grid=True, **kwargs)
    return infer(image, instruction, **kwargs)


def equipment_frame(image):
    """Return the tested fixed-grid canvas and its exact unpadding geometry."""
    resized = ImageOps.contain(image, (320, 320), method=Image.Resampling.BICUBIC)
    left, top = (320 - resized.width) // 2, (320 - resized.height) // 2
    canvas = Image.new("RGB", (320, 320), (128, 128, 128))
    canvas.paste(resized, (left, top))
    return canvas, (left, top, resized.width, resized.height)


def restore_equipment_boxes(boxes, geometry):
    left, top, width, height = geometry
    restored = []
    for box in boxes:
        x, y, w, h = box["bbox"]
        x1, y1 = max(left, x * 320), max(top, y * 320)
        x2, y2 = min(left + width, (x + w) * 320), min(top + height, (y + h) * 320)
        if x2 > x1 and y2 > y1:
            restored.append({"label": box["label"], "bbox": [
                (x1 - left) / width, (y1 - top) / height, (x2 - x1) / width, (y2 - y1) / height]})
    return restored


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
            text = infer_equipment(image.crop((left, top, right, bottom)),
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
    if YOLO_SERVER:
        return inspect_yolo(image, items)
    return semantic_equipment_image(image,items)


@app.post("/inspect_semantic")
def inspect_semantic(file: UploadFile = File(...), labels: str = Form(...)):
    try:
        items=json.loads(labels)
        if (not isinstance(items,dict) or not 1<=len(items)<=20 or any(
                not isinstance(k,str) or not isinstance(v,str) or not 0<len(k)<=80 or not 0<len(v)<=80
                for k,v in items.items())):
            raise ValueError()
    except ValueError as exc:
        file.file.close()
        raise HTTPException(422,"器材清单无效") from exc
    return semantic_equipment_image(read_upload(file),items,partial_boxes=True)


def semantic_equipment_image(image,items,partial_boxes=False):
    geometry = None
    if EQUIPMENT_SERVER:
        image, geometry = equipment_frame(image)
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
    text = infer_equipment(image, prompt, tokens=512, deadline=deadline)
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
        if partial_boxes:
            boxes.extend({"label": key, "bbox": box["bbox"],
                          "model_class":"red_probe" if box["label"]==parts[0] else "black_probe"}
                         for box in parsed["boxes"] if box["label"] in parts)
        elif present[key] is True:
            boxes.extend({"label": key, "bbox": box["bbox"]} for box in parsed["boxes"] if box["label"] in parts)
    if geometry is not None:
        boxes = restore_equipment_boxes(boxes, geometry)
        for key in present:
            minimum_boxes = 2 if key in groups else 1
            if present[key] is True and sum(box["label"] == key for box in boxes) < minimum_boxes:
                present[key] = None
    return {"present": present, "boxes": boxes, "coordinate_space": "normalized_xywh", "raw_text": text,
            "backend": EQUIPMENT_BACKEND if EQUIPMENT_SERVER else BACKEND, "requires_confirmation": True}


STEP_PROMPTS = {
    "meter_setup": (
        'Inspect the actual multimeter rotary selector and the actual lead plugs in this image. '
        'Return ONLY JSON: {"checks":{"black_in_com":true|false|null,"red_in_voltage":true|false|null,'
        '"dc_voltage_mode":true|false|null},"meter_range":"dc_200mv"|"dc_2v"|"dc_20v"|null}. '
        'black_in_com means the black lead plug is visibly inserted into the socket labeled COM. '
        'red_in_voltage means the red lead plug is visibly in the V or V/ohm socket, not A or mA. '
        'dc_voltage_mode is true only when the selector visibly points to a DC voltage range. '
        'Determine meter_range from the pointer position and printed range text, not from LCD digits. '
        'If another range, AC mode or current mode is selected, do not report a supported DC range. '
        'If a plug, printed socket label, selector pointer or range cannot be seen clearly, return null '
        'for that item. Do not assume the setup is correct because a battery is present.'
    ),
    "battery_contact": (
        'Inspect the actual contact of the handheld probe metal tips with the two battery terminals. '
        'Return ONLY JSON: {"checks":{"red_on_positive":true|false|null,'
        '"black_on_negative":true|false|null,"no_short":true|false|null}}. '
        'Verify red tip touches the positive terminal and black tip touches the negative terminal. '
        'no_short is true only if the visible metal tips are separate and do not bridge the two '
        'terminals or touch each other. A wire passing nearby or a probe merely near the battery '
        'does not establish electrical contact. If fingers, framing or blur hide the tips or terminal '
        'polarity, return null for affected checks. Do not infer correct contact from a nonzero LCD reading.'
    ),
    "battery_cleanup": (
        'Inspect whether the experiment has been physically cleaned up. Return ONLY JSON: '
        '{"checks":{"probes_removed":true|false|null,"meter_off":true|false|null}}. '
        'probes_removed is true only when both handheld metal probe tips are visibly away from the '
        'battery terminals; if the probes or battery are outside the frame use null. '
        'meter_off is true only when the actual selector visibly points to OFF or the actual power '
        'control visibly indicates off. A blank, dark or unreadable LCD alone does not prove off. '
        'If a check is clearly not satisfied return false; if hidden or uncertain return null.'
    ),
}


@app.post("/check_step")
def check_step(file: UploadFile = File(...), rule: str = Form(...)):
    if rule not in STEP_PROMPTS:
        file.file.close()
        raise HTTPException(422, "未知视觉检查规则")
    text = infer(read_upload(file), STEP_PROMPTS[rule], tokens=256, max_side=1120)
    content = text.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(content)
        if not isinstance(data, dict) or not isinstance(data.get("checks"), dict):
            raise ValueError()
    except ValueError:
        data = {"checks": {}}
    return {"checks": data["checks"], "meter_range": data.get("meter_range"),
            "raw_text": text, "backend": BACKEND, "rule": rule}


@app.post("/check_meter_live")
def check_meter_live(file: UploadFile = File(...)):
    from labagent.meter_live_model import KEYS, detail_crops, infer_closeups, meter_region
    image = read_upload(file)
    if not METER_SERVER or not YOLO_SERVER:
        raise HTTPException(503, "持续仪表检查尚未配置，可使用人工核对")
    if not METER_LOCK.acquire(blocking=False):
        raise HTTPException(429, "仪表检查正在处理上一张画面，请稍候")
    started = time.perf_counter()
    try:
        detected = inspect_yolo(image, {"multimeter": "数字万用表"})
        crop, roi = meter_region(image, detected)
        if crop is None or detail_crops(crop) is None:
            if crop is None:
                reason = "meter_not_localized"
                hint = "未定位到唯一且足够大的万用表，请将一台仪表完整靠近镜头，避免遮挡。"
            elif crop.width >= crop.height * 0.95:
                reason = "meter_not_upright"
                hint = "仪表当前横向或倾斜过大，请正向竖放：显示屏在上、插孔在下。"
            else:
                reason = "meter_detail_too_small"
                hint = "仪表在当前画面中过小，旋钮或插孔细节不足；请靠近镜头，保持整台仪表完整入镜。"
            return {"checks": dict.fromkeys(KEYS), "meter_range": None, "roi": None,
                    "backend": "qwen3-vl-cix-npu-vision-cpu-text-320-closeup-v1",
                    "model_used": False,
                    "hint": hint, "diagnostic": {"reason": reason, "frame_size": list(image.size),
                        "meter_size": list(crop.size) if crop is not None else None}}
        worker = meter_worker_state()
        answer = infer_closeups(crop, METER_SERVER, OPENER)
        after = meter_worker_state()
        if after.get("requests", 0) < worker.get("requests", 0) + 2:
            raise ValueError("Both close-ups must use the NPU encoder")
        for view in answer["detail_views"]:
            x, y, w, h = view["roi"]
            view["roi"] = [roi[0] + x * roi[2], roi[1] + y * roi[3], w * roi[2], h * roi[3]]
        return {**answer, "roi": roi, "backend": "qwen3-vl-cix-npu-vision-cpu-text-320-closeup-v1",
                "model_used": True, "vision_ms": after.get("last_duration_ms"),
                "total_ms": round((time.perf_counter()-started)*1000, 1)}
    except HTTPException:
        raise
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(503, "持续仪表检查未完成，请稍候或人工核对") from exc
    finally:
        METER_LOCK.release()


def meter_worker_state():
    state = json.loads(NPU_STATUS.read_text())
    if not state.get("ready") or not Path(state["socket"]).is_socket():
        raise ValueError("NPU encoder unavailable")
    os.kill(int(state["pid"]), 0)
    return state


@app.post("/recognize")
def recognize(file: UploadFile = File(...), fast_only: bool = Form(False)):
    image = read_upload(file)
    ocr = None
    if LCD_READER:
        try:
            ocr = LCD_READER.read(image)
            if ocr["accepted"] or not ocr["text"]:
                reading = ocr["text"] or None
                text = json.dumps({"reading": reading, "unit": None, "ocr_check": ocr["reason"]}, ensure_ascii=False)
                return {"text": text, "confidence": ocr["confidence"], "backend": ocr["backend"],
                        "ocr": ocr, "fallback_used": False}
        except Exception:
            logging.getLogger(__name__).exception("LCD OCR failed; using VLM fallback")
    if fast_only is True:
        reason = ocr.get("reason", "fast_ocr_uncertain") if ocr else "fast_ocr_unavailable"
        return {"text": json.dumps({"reading": None, "unit": None, "reason": reason}),
                "confidence": 0, "backend": ocr.get("backend", "lcd-ocr") if ocr else "lcd-ocr",
                "fallback_used": False}
    text = infer(image,
                 'Read only the large LCD digits in this image. Return ONLY JSON: '
                 '{"reading":"exact digit string including sign and decimal point","unit":null}. '
                 'If unreadable, use {"reading":null,"unit":null}. '
                 'Keep a visible leading decimal point, for example .419; do not shift it. '
                 'Ignore model numbers, dial markings and printed range numbers. '
                 'Do not infer missing digits or units.', tokens=96, max_side=1120)
    vlm_raw_text = text
    if LCD_READER:
        # Reject positive pixel evidence contradicting the fallback as well.
        from labagent.lcd_reader import NUMBER, check_numeric
        try:
            content = text.strip()
            if content.startswith('```'):
                content = content.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
            parsed_fallback = json.loads(content)
            value = parsed_fallback.get('reading')
            if isinstance(value, str):
                value = value.replace('−','-').replace('–','-')
            unit = parsed_fallback.get('unit')
            evidence = ocr['evidence'] if ocr else {}
            reason = 'vlm_fallback'
            if isinstance(value, str) and value.count(',') == 1 and '.' not in value:
                normalized = value.replace(',','.')
                if check_numeric(normalized, evidence)[0]:
                    value = normalized
            if isinstance(value, str) and evidence.get('decimal_after') == 0:
                normalized = ('-' if value.startswith('-') else '') + value.lstrip('+-')[1:] if value.lstrip('+-').startswith('0.') else value
                if check_numeric(normalized, evidence)[0]:
                    value = normalized
            if (isinstance(value, str) and len(value) <= 25 and NUMBER.fullmatch(value)
                    and len(value.lstrip('+-').replace('.','')) >= 2 and unit in {None,'V','mV'}):
                okay, reason = check_numeric(value, evidence)
                definite = evidence.get('certain') and evidence.get('digit_count') == len(value.lstrip('+-').replace('.',''))
                contradicted = definite and ((reason == 'decimal_position_mismatch' and evidence.get('decimal_after') is not None)
                    or (evidence.get('minus') is True and not value.startswith('-')))
                ocr_text = ocr.get('text', '') if ocr else ''
                digits_disagree = (NUMBER.fullmatch(ocr_text)
                    and value.lstrip('+-').replace('.', '') != ocr_text.lstrip('+-').replace('.', ''))
                if digits_disagree:
                    value, reason = None, 'reader_disagreement_on_digits'
                elif not okay and contradicted:
                    value = None
                elif (not okay and ocr and NUMBER.fullmatch(ocr.get('text',''))
                      and (Decimal(value) != Decimal(ocr['text']) or value.startswith('-') != ocr['text'].startswith('-'))):
                    value, reason = None, 'reader_disagreement_without_pixel_confirmation'
            else:
                value, unit, reason = None, None, 'unconfirmed_fallback_reading'
            text = json.dumps({'reading':value,'unit':unit,'reason':reason},ensure_ascii=False)
        except (ValueError, AttributeError, TypeError):
            text = json.dumps({'reading':None,'unit':None,'reason':'invalid_fallback_json'},ensure_ascii=False)
    return {"text": text, "confidence": 0, "backend": BACKEND,
            "fallback_used": LCD_READER is not None, "ocr": ocr, 'vlm_raw_text':vlm_raw_text}


@app.post("/locate_display")
def locate_display(file: UploadFile = File(...), fast_only: bool = Form(False)):
    image = read_upload(file)
    if LCD_READER:
        try:
            from labagent.lcd_reader import locate_lcd
            roi = locate_lcd(image)
            if roi:
                return {"roi":roi,"backend":"opencv-adaptive-lcd-region",
                        "raw_text":"已定位显示屏区域，随后从原图裁剪读取"}
        except (ImportError, OSError, ValueError):
            pass
    try:
        from labagent.vision_regions import lcd_region
        roi = lcd_region(image)
    except (ImportError, OSError, ValueError):
        roi = None
    if roi:
        return {"roi": roi, "backend": "opencv-lcd-region",
                "raw_text": "自动检测到显示屏区域，随后从原图裁剪读取"}
    if fast_only is True:
        meter_present = None
        if YOLO_SERVER:
            try:
                from labagent.lcd_reader import locate_lcd
                detected = inspect_yolo(image, {"multimeter": "数字万用表"}, timeout=5)
                meter_present = detected.get("present", {}).get("multimeter")
                regions = [box for box in detected.get("boxes", []) if box.get("label") == "multimeter"]
                roi = locate_lcd(image, meter_regions=regions)
                if roi:
                    return {"roi": roi, "backend": "yolo-opencv-lcd-region",
                            "raw_text": "根据仪表检测框快速定位显示屏"}
            except (HTTPException, ImportError, ValueError, OSError):
                pass
        return {"roi": None, "meter_present": meter_present,
                "raw_text": "快速定位未找到显示屏，请调整构图后重新采集",
                "backend": "opencv-lcd-region"}
    text = infer(image, 'Locate the multimeter LCD screen only. Return ONLY [left,top,right,bottom] '
                 'normalized to 0-1000 relative to the whole image. Include the LCD digits and sign, '
                 'exclude the dial. If uncertain return null.', tokens=64)
    return {"roi": parse_display_box(text), "raw_text": text, "backend": BACKEND}

