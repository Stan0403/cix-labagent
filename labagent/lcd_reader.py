"""Fine-tuned LCD OCR with independent pixel checks for sign and decimal placement."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import threading
import time

import cv2
import numpy as np

BACKEND = "ppocr-lcd-v3-user81-onnxruntime-cpu"
NUMBER = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$")


def punctuation_evidence(image):
    """Use source-resolution contrast, not OCR logits, to verify punctuation.

    Uncertain geometry is rejected, never used to invent or move a decimal.
    No fixed camera coordinates or expected voltage values are used.
    """
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    if min(height, width) < 24:
        return {"certain": False, "reason": "display_too_small"}
    kernel = max(9, int(height * 0.12) | 1)
    contrast = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, np.ones((kernel, kernel), np.uint8))
    threshold, mask = cv2.threshold(contrast, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if threshold < 6:
        return {"certain": False, "reason": "low_local_contrast"}
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    for i in range(1, count):
        x, y, w, h, _ = stats[i]
        if x == 0 or y == 0 or x + w >= width or y + h >= height:
            mask[labels == i] = 0

    def digit_components(binary):
        joined = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((7, 3), np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(joined)
        ids = [i for i in range(1, count) if stats[i, 3] > height * 0.3 and stats[i, 4] > 20]
        return labels, stats, sorted(ids, key=lambda i: stats[i, 0])

    labels, stats, ids = digit_components(mask)
    if not 2 <= len(ids) <= 6:
        return {"certain": False, "reason": "digit_geometry_uncertain", "digit_count": len(ids)}
    points = np.vstack([np.column_stack(np.where(labels == i)[::-1]) for i in ids]).astype(np.float32)
    rect = cv2.minAreaRect(points)
    angle = rect[2] - (90 if rect[1][0] < rect[1][1] else 0)
    if abs(angle) > 25:
        return {"certain": False, "reason": "display_angle_uncertain"}
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1)
    out_width = int(width * abs(matrix[0, 0]) + height * abs(matrix[0, 1])) + 2
    out_height = int(height * abs(matrix[0, 0]) + width * abs(matrix[0, 1])) + 2
    matrix[0, 2] += out_width / 2 - width / 2
    matrix[1, 2] += out_height / 2 - height / 2
    mask = cv2.warpAffine(mask, matrix, (out_width, out_height), flags=cv2.INTER_NEAREST)
    _, stats, ids = digit_components(mask)
    if not 2 <= len(ids) <= 6:
        return {"certain": False, "reason": "deskew_digit_geometry_uncertain"}
    boxes = [stats[i] for i in ids]
    glyph_height = float(np.median([b[3] for b in boxes]))
    baseline = float(np.median([b[1] + b[3] - 1 for b in boxes]))
    centers = [float(b[0] + b[2] / 2) for b in boxes]
    top = float(np.median([b[1] for b in boxes]))
    count, _, small, centroids = cv2.connectedComponentsWithStats(mask)
    decimals, minus = set(), False
    for i in range(1, count):
        x, y, w, h, area = [int(v) for v in small[i]]
        cx, cy = [float(v) for v in centroids[i]]
        if (
            area >= 3
            and w <= glyph_height * 0.30
            and h <= glyph_height * 0.32
            and 0.35 <= w / max(1, h) <= 2.5
            and abs(cy - baseline) <= glyph_height * 0.22
        ):
            decimals.add(sum(cx > center for center in centers))
        if (
            area >= 5
            and cx < centers[0]
            and w >= glyph_height * 0.20
            and h <= glyph_height * 0.30
            and w / max(1, h) >= 1.7
            and abs(cy - (top + baseline) / 2) <= glyph_height * 0.25
        ):
            minus = True
    if len(decimals) > 1:
        return {"certain": False, "reason": "multiple_decimal_regions", "decimal_regions": sorted(decimals)}
    if decimals and max(decimals) == len(ids):
        return {"certain": False, "reason": "trailing_decimal_region_unreliable"}
    return {
        "certain": True,
        "digit_count": len(ids),
        "minus": minus,
        "decimal_after": next(iter(decimals)) if decimals else None,
        "angle": round(float(angle), 2),
    }


def check_numeric(text, evidence):
    if not NUMBER.fullmatch(text):
        return False, "numeric_syntax"
    if not evidence.get("certain"):
        return False, evidence.get("reason", "punctuation_uncertain")
    unsigned = text.lstrip("+-")
    digits = unsigned.replace(".", "")
    if len(digits) != evidence["digit_count"]:
        return False, "digit_count_mismatch"
    if text.startswith("-") != evidence["minus"]:
        return False, "sign_mismatch"
    decimal_after = unsigned.index(".") if "." in unsigned else None
    if decimal_after != evidence["decimal_after"]:
        return False, "decimal_position_mismatch"
    return True, "source_pixels_consistent"


def locate_lcd(image, meter_regions=None):
    """Contrast-adaptive LCD proposal inside a detected meter, including dim LCDs."""
    from .vision_regions import proposals, _components

    rgb = np.asarray(image.convert("RGB"))
    regions = meter_regions if meter_regions is not None else proposals(image, {"multimeter": "万用表"})
    for region in regions:
        x, y, w, h = region["bbox"]
        left, top = int(x * image.width), int(y * image.height)
        right, bottom = int((x + w) * image.width), int((y + h * 0.58) * image.height)
        crop = rgb[top:bottom, left:right]
        if crop.size == 0:
            continue
        hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
        for saturation in (145, 160, 180):
            components = _components(
                cv2,
                np,
                hsv,
                (35, 8, 45),
                (125, saturation, 245),
                crop.shape[0] * crop.shape[1] * 0.025,
                crop.shape[0] * crop.shape[1] * 0.75,
            )
            for _, (cx, cy, cw, ch), contour in components:
                if not 1.3 < cw / ch < 5 or cw < crop.shape[1] * 0.25 or ch < 12:
                    continue
                gray = cv2.cvtColor(crop[cy : cy + ch, cx : cx + cw], cv2.COLOR_RGB2GRAY)
                cutoff, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                fraction = float((gray <= cutoff).mean())
                if not 0.035 < fraction < 0.65:
                    continue
                margin = max(3, min(cw, ch) * 0.04)
                x1, y1 = max(0, left + cx - margin), max(0, top + cy - margin)
                x2, y2 = min(image.width, left + cx + cw + margin), min(image.height, top + cy + ch + margin)
                return [
                    x1 / image.width,
                    y1 / image.height,
                    (x2 - x1) / image.width,
                    (y2 - y1) / image.height,
                ]
    return None


class LcdReader:
    def __init__(self, model_path, dictionary_path, threads=4):
        import onnxruntime as ort

        self.characters = [""] + Path(dictionary_path).read_text(encoding="utf-8").splitlines() + [" "]
        if len(self.characters) != 6625:
            raise ValueError("Unexpected OCR dictionary")
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
        self.model_sha256 = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
        self.lock = threading.Lock()

    def predict(self, image, interpolation=cv2.INTER_LINEAR):
        bgr = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
        height, width = bgr.shape[:2]
        resized_width = min(400, int(np.ceil(width * 32 / height)))
        resized = (
            cv2.resize(bgr, (resized_width, 32), interpolation=interpolation).astype(np.float32) / 127.5 - 1
        )
        tensor = np.zeros((1, 3, 32, 400), np.float32)
        tensor[0, :, :, :resized_width] = resized.transpose(2, 0, 1)
        with self.lock:
            probabilities = self.session.run(None, {"x": tensor})[0]
        if probabilities.shape != (1, 100, 6625) or not np.isfinite(probabilities).all():
            raise ValueError("Invalid OCR output")
        indexes = probabilities[0].argmax(-1)
        positions = [i for i, value in enumerate(indexes) if value and (i == 0 or value != indexes[i - 1])]
        text = "".join(self.characters[int(indexes[i])] for i in positions)
        confidence = (
            float(np.mean([probabilities[0, i, indexes[i]] for i in positions])) if positions else 0.0
        )
        minimum = float(min(probabilities[0, i, indexes[i]] for i in positions)) if positions else 0.0
        return text, confidence, minimum

    def read(self, image):
        start = time.perf_counter()
        text, confidence, minimum = self.predict(image)
        evidence = punctuation_evidence(image) if text else {"certain": False, "reason": "no_ocr_text"}
        accepted, reason = check_numeric(text, evidence)
        # Absence of a tiny detected dot/sign is not positive evidence of absence.
        # For inconclusive pixel geometry, require high-confidence agreement under
        # two resampling methods. A positive, conflicting decimal/sign veto wins.
        contradiction = (
            evidence.get("certain")
            and evidence.get("digit_count") == len(text.lstrip("+-").replace(".", ""))
            and (
                (evidence.get("decimal_after") is not None and reason == "decimal_position_mismatch")
                or (evidence.get("minus") is True and not text.startswith("-"))
            )
        )
        alternate = None
        if (
            not accepted
            and not contradiction
            and NUMBER.fullmatch(text)
            and len(text.lstrip("+-").replace(".", "")) >= 2
            and confidence >= 0.95
            and minimum >= 0.80
        ):
            alternate, alternate_confidence, _ = self.predict(image, cv2.INTER_AREA)
            if alternate == text and alternate_confidence >= 0.90:
                accepted, reason = True, "high_confidence_two_resamplings_consistent"
        if text and len(text.lstrip("+-").replace(".", "")) < 2:
            accepted, reason = False, "single_digit_or_overrange"
        if accepted and confidence < 0.80:
            accepted, reason = False, "low_ocr_confidence"
        return {
            "text": text,
            "confidence": confidence,
            "accepted": accepted,
            "reason": reason,
            "evidence": evidence,
            "alternate_text": alternate,
            "backend": BACKEND,
            "seconds": time.perf_counter() - start,
        }
