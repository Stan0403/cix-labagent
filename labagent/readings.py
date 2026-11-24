"""Strict candidate extraction; never equate extraction with measurement approval."""
import json
import math
import re


def valid_boxes(boxes, labels):
    result = []
    if not isinstance(boxes, list):
        return result
    for item in boxes[:40]:
        if not isinstance(item, dict) or item.get("label") not in labels:
            continue
        box = item.get("bbox")
        if not isinstance(box, list) or len(box) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
            continue
        x, y, w, h = box
        if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x+w <= 1.00001 and y+h <= 1.00001):
            continue
        entry = {"label": item["label"], "bbox": box}
        model_class = item.get("model_class")
        if isinstance(model_class, str) and model_class in {"multimeter", "battery", "red_probe", "black_probe"}:
            entry["model_class"] = model_class
        result.append(entry)
    return result


def equipment_result(data, labels):
    if not isinstance(data, dict):
        return {"present": {key: None for key in labels}, "boxes": []}
    raw_presence = data.get("present", data)
    if not isinstance(raw_presence, dict):
        raw_presence = {}
    present = {key: raw_presence.get(key) if isinstance(raw_presence.get(key), bool) else None for key in labels}
    normalized = []
    raw_boxes = data.get("boxes", [])
    if isinstance(raw_boxes, list):
        for item in raw_boxes[:40]:
            if not isinstance(item, dict) or item.get("label") not in labels or present[item["label"]] is False:
                continue
            box = item.get("bbox")
            if not isinstance(box, list) or len(box) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
                continue
            left, top, right, bottom = box
            normalized.append({"label": item["label"], "bbox": [left/1000, top/1000, (right-left)/1000, (bottom-top)/1000]})
    boxes = valid_boxes(normalized, labels)
    for box in boxes:
        if present[box["label"]] is None:
            present[box["label"]] = True
    return {"present": present, "boxes": boxes}


def parse_equipment_text(text, labels):
    content = text.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        return equipment_result(json.loads(content), labels)
    except ValueError:
        pass
    present, boxes = {}, []
    reverse = {name: key for key, name in labels.items()}
    for line in content.splitlines():
        match = re.fullmatch(r"\s*[-*]?\s*([^:：|]+)\s*[:：|]\s*(.*?)\s*", line)
        if not match:
            continue
        key = match.group(1).strip().strip('"')
        key = key if key in labels else reverse.get(key)
        if key is None:
            continue
        value = match.group(2).strip()
        if value.lower() in {"missing", "absent", "false", "not visible"}:
            present[key] = False
            continue
        numbers = re.fullmatch(r"[\[(]?\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*[\])]?", value)
        if numbers:
            boxes.append({"label": key, "bbox": [float(x) for x in numbers.groups()]})
    return equipment_result({"present": present, "boxes": boxes}, labels)


def parse_vlm_detections(text, labels, width, height):
    """Parse Qwen2.5-VL pixel-coordinate JSON without inventing absent objects."""
    content = text.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data, end = json.JSONDecoder().raw_decode(content)
        if content[end:].strip().strip("]}").strip():
            raise ValueError()
    except ValueError:
        return {"present": {key: None for key in labels}, "boxes": []}
    items = data if isinstance(data, list) else data.get("objects", [data]) if isinstance(data, dict) else []
    present = {key: None for key in labels}
    boxes = []
    reverse = {name: key for key, name in labels.items()}
    for item in items[:40]:
        if isinstance(item, str) and item in labels:
            present[item] = True
            continue
        if not isinstance(item, dict):
            continue
        name = item.get("label")
        key = name if isinstance(name, str) and name in labels else reverse.get(name) if isinstance(name, str) else None
        if key is None:
            continue
        if item.get("present") is False:
            present[key] = False
            continue
        box = item.get("bbox_2d")
        if not isinstance(box, list) or len(box) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
            continue
        x1, y1, x2, y2 = box
        candidate = valid_boxes([{"label": key, "bbox": [x1/width, y1/height, (x2-x1)/width, (y2-y1)/height]}], labels)
        if candidate:
            present[key] = True
            boxes.extend(candidate)
    return {"present": present, "boxes": boxes}


def parse_display_box(text):
    content = text.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        data = json.loads(content)
        box = data.get("bbox") if isinstance(data, dict) else data
    except ValueError:
        matches = re.findall(r"[\[(]\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*[\])]", content)
        box = [float(x) for x in matches[0]] if len(matches) == 1 else None
    parsed = equipment_result({"boxes": [{"label": "display", "bbox": box}]}, {"display": "显示屏"})
    return parsed["boxes"][0]["bbox"] if parsed["boxes"] else None


def parse_candidate(text, meter_range=""):
    raw = text.strip().replace("−", "-").replace("–", "-")[:2000]
    content = raw
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    unit = None
    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            value = obj.get("reading", obj.get("value"))
            if value is None or isinstance(value, bool):
                return {"value": None, "reason": "模型无法确认读数，请重拍或人工核对"}
            content = str(value)
            unit = obj.get("unit")
    except (ValueError, TypeError):
        pass
    if re.fullmatch(r"\s*(?:OL|O\.L\.?|OVER|---+)\s*", content, re.I):
        return {"value": None, "reason": "显示超量程或无法读取，请核对量程后重拍"}
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(mV|V)?\s*", content, re.I)
    if not match:
        # Backward-compatible descriptions must contain exactly one number and explicit display context.
        numbers = re.findall(r"(?<![\w.])[+-]?\d+(?:\.\d+)?(?!\w)", content)
        if len(numbers) != 1 or not re.search(r"display|reading|number|读数|显示", content, re.I):
            return {"value": None, "reason": "没有唯一且明确的显示屏数字，请人工核对"}
        numeral = numbers[0]
    else:
        numeral = match.group(1)
        unit = match.group(2) or unit
    units = {"dc_200mv": "mV", "dc_2v": "V", "dc_20v": "V"}
    unit = unit or units.get(meter_range)
    if isinstance(unit, str):
        unit = "mV" if unit.lower() == "mv" else "V" if unit.lower() == "v" else unit
    if unit not in {"V", "mV"}:
        return {"value": None, "reason": "缺少单位，请先核对直流电压档位"}
    value = float(numeral) / (1000 if unit == "mV" else 1)
    if not math.isfinite(value):
        return {"value": None, "reason": "数值无效"}
    limit = {"dc_200mv": .2, "dc_2v": 2, "dc_20v": 20}.get(meter_range, 20)
    if abs(value) > limit:
        return {"value": None, "reason": "数字超出已确认量程，可能有小数点误读，请重拍"}
    return {"value": value, "reason": "待人工核对符号、小数点及量程；识别成功不代表已保存"}
