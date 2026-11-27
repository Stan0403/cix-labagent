"""Small, explicit meter-only vision replies; no setting is inferred from LCD digits."""

import base64
import io
import json
import re
import time
import urllib.request

from PIL import ImageOps

PARTS = {
    "dial": {
        "box": (0.0, 0.26, 1.0, 0.79),
        "prompt": (
            "Close-up of a multimeter selector and its printed scales. Output exactly two characters. "
            "First: selector indicator visibly points to DC VOLTAGE: Y=yes,N=no,?=unclear. "
            "Second: printed range at that indicator: m=200mV,2=2V,v=20V,?=other/off/unclear. "
            "Follow the indicator to its printed tick, not the opposite end of the handle. "
            "Do not infer settings from LCD digits or assume the desired setting. "
            "An illegible indicator, symbol or scale MUST be ?. Example ??. No explanation."
        ),
        "grammar": "root ::= [YN?] [m2v?]",
        "pattern": r"[YN?][m2v?]",
    },
    "terminals": {
        "box": (0.0, 0.67, 1.0, 1.0),
        "prompt": (
            "Close-up of multimeter terminals, plugs and socket labels. Output exactly two characters. "
            "First: black plug visibly inserted into socket labeled COM. "
            "Second: red plug visibly inserted into socket labeled V/ohm, not A or mA. "
            "Y=yes,N=visibly wrong,?=unclear. Read the actual labels and insertion positions. "
            "A cable crossing a socket is not proof of insertion. Hidden plugs or illegible labels "
            "MUST be ?. Do not assume the desired wiring. Example ??. No explanation."
        ),
        "grammar": "root ::= [YN?] [YN?]",
        "pattern": r"[YN?]{2}",
    },
}
KEYS = ("black_in_com", "red_in_voltage", "dc_voltage_mode")


def decode_reply(text):
    if not isinstance(text, str) or not re.fullmatch(r"[YN?]{3}[m2v?]", text.strip()):
        raise ValueError("Invalid compact meter reply")
    code = text.strip()
    return {
        "checks": {k: {"Y": True, "N": False, "?": None}[v] for k, v in zip(KEYS, code)},
        "meter_range": {"m": "dc_200mv", "2": "dc_2v", "v": "dc_20v", "?": None}[code[-1]],
    }


def meter_region(image, detections):
    boxes = detections.get("boxes", []) if isinstance(detections, dict) else []
    boxes = [b.get("bbox") for b in boxes if b.get("label") == "multimeter"]
    boxes = [
        b
        for b in boxes
        if isinstance(b, list)
        and len(b) == 4
        and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in b)
        and b[2] > 0
        and b[3] > 0
        and b[0] + b[2] <= 1.00001
        and b[1] + b[3] <= 1.00001
    ]
    # Multiple instruments are ambiguous; do not silently choose a different meter.
    if len(boxes) != 1:
        return None, None
    x, y, w, h = boxes[0]
    if w * image.width < 150 or h * image.height < 220:
        return None, None
    x1, y1 = max(0, x - w * 0.04), max(0, y - h * 0.04)
    x2, y2 = min(1, x + w * 1.04), min(1, y + h * 1.04)
    region = [x1, y1, x2 - x1, y2 - y1]
    crop = image.crop(
        (round(x1 * image.width), round(y1 * image.height), round(x2 * image.width), round(y2 * image.height))
    )
    return crop, region


def detail_crops(image):
    """An upright handheld meter profile; cropping cannot restore absent source detail."""
    if image.width >= image.height * 0.95:
        return None
    result = {}
    for part, spec in PARTS.items():
        x1, y1, x2, y2 = spec["box"]
        crop = image.crop(
            (
                round(x1 * image.width),
                round(y1 * image.height),
                round(x2 * image.width),
                round(y2 * image.height),
            )
        )
        if crop.width < 180 or crop.height < 90:
            return None
        result[part] = crop
    return result


def infer_part(image, part, server, opener):
    spec = PARTS[part]
    frame = ImageOps.pad(image, (320, 320), color=(128, 128, 128))
    data = io.BytesIO()
    frame.save(data, format="JPEG", quality=95)
    image_url = "data:image/jpeg;base64," + base64.b64encode(data.getvalue()).decode()
    payload = {
        "model": "qwen3-vl-4b-local",
        "temperature": 0,
        "cache_prompt": False,
        "max_tokens": 8,
        "grammar": spec["grammar"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": spec["prompt"]},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    },
                ],
            }
        ],
    }
    request = urllib.request.Request(
        server + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with opener.open(request, timeout=30) as response:
        result = json.load(response)
    choice = result["choices"][0]
    if choice.get("finish_reason") != "stop":
        raise ValueError("Incomplete compact meter reply")
    code = choice["message"].get("content")
    if not isinstance(code, str) or not re.fullmatch(spec["pattern"], code.strip()):
        raise ValueError("Invalid close-up meter reply")
    x1, y1, x2, y2 = spec["box"]
    return {"part": part, "code": code.strip(), "image_url": image_url, "roi": [x1, y1, x2 - x1, y2 - y1]}


def infer_closeups(image, server, opener):
    crops = detail_crops(image)
    if crops is None:
        raise ValueError("Show one upright meter closer to the camera")
    started = time.perf_counter()
    views = [infer_part(crops[part], part, server, opener) for part in PARTS]
    replies = {view["part"]: view["code"] for view in views}
    return {
        **decode_reply(replies["terminals"] + replies["dial"]),
        "inference_ms": round((time.perf_counter() - started) * 1000, 1),
        "detail_mode": "separate-dial-terminals-v1",
        "detail_views": views,
    }
