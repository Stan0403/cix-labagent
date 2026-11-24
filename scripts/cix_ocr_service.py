"""Local PP-OCRv4 service with CIX NPU models and aspect-preserving recognition."""
import math
import os
import sys
import threading
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from NOE_Engine import EngineInfer

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "models" / "ppocr-cix"
sys.path.insert(0, str(MODEL))
os.chdir(MODEL)  # The official recognizer loads its character dictionary relative to cwd.
from pp_ocr import TextClassifier, TextDetector, TextRecognizer, get_rotate_crop_image, sorted_boxes  # noqa: E402

app = FastAPI()
lock = threading.Lock()
models = {}


@app.on_event("startup")
def startup():
    for name, filename in (("det", "PP-OCRv4_det.cix"), ("cls", "cls.cix"), ("rec", "rec.cix")):
        models[name] = EngineInfer(str(MODEL / filename))


@app.on_event("shutdown")
def shutdown():
    for model in models.values():
        model.clean()


@app.get("/health")
def health():
    return {"ready": len(models) == 3, "backend": "CIX NPU PP-OCRv4"}


def recognition_tensor(crop):
    """Keep glyph geometry within the exported recognizer's fixed 3x32x400 input."""
    height, width = crop.shape[:2]
    resized_width = min(400, math.ceil(32 * width / height))
    resized = cv2.resize(crop, (resized_width, 32))
    normalized = resized.astype(np.float32).transpose(2, 0, 1) / 127.5 - 1
    # The bundled reference forces resized_width=400, distorting short numbers.
    # Pad after normalization, as in PaddleOCR, instead of stretching each crop.
    padded = np.zeros((1, 3, 32, 400), dtype=np.float32)
    padded[0, :, :, :resized_width] = normalized
    return padded


@app.post("/recognize")
async def recognize(file: UploadFile = File(...)):
    raw = await file.read(8_000_001)
    if len(raw) > 8_000_000:
        raise HTTPException(413, "image too large")
    img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None or img.shape[0] * img.shape[1] > 20_000_000:
        raise HTTPException(400, "invalid image")
    with lock:
        # The detector has a fixed portrait input. Preserve the display's aspect
        # ratio instead of stretching a wide meter screen into that shape.
        scale = min(720 / img.shape[1], 1150 / img.shape[0])
        resized = cv2.resize(img, (max(1, round(img.shape[1] * scale)),
                                   max(1, round(img.shape[0] * scale))))
        canvas = np.full((1150, 720, 3), 255, dtype=np.uint8)
        y = (1150 - resized.shape[0]) // 2
        x = (720 - resized.shape[1]) // 2
        canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
        img = canvas
        detector, classifier, recognizer = TextDetector(), TextClassifier(), TextRecognizer()
        tensor, shape = detector.preprocess_det(img)
        output = np.reshape(models["det"].forward([tensor])[0], (1, 1, 960, 608))
        boxes = sorted_boxes(detector.postprocess_det(output, shape, img))
        crops = [get_rotate_crop_image(img, box.copy()) for box in boxes[:20]]
        if not crops:
            return {"text": "", "confidence": 0, "backend": "cix-ppocrv4"}
        classifier.preprocess_cls(crops)
        for i in range(len(crops)):
            tensor = classifier.preprocess_cls_onnx(crops, i)
            output = np.reshape(models["cls"].forward([tensor])[0], (1, 2))
            classifier.postprocess_cls(output, classifier.indices, i, classifier.cls_res, crops)
        recognizer.preprocess_rec(crops)
        for i in range(len(crops)):
            tensor = recognition_tensor(crops[recognizer.indices[i]])
            output = np.reshape(models["rec"].forward([tensor])[0], (1, 100, 6625)).astype(np.float32)
            output = np.exp(output - output.max(axis=2, keepdims=True))
            output /= output.sum(axis=2, keepdims=True)
            result = recognizer.postprocess_op(output)
            for j, item in enumerate(result):
                recognizer.rec_res[recognizer.indices[i + j]] = item
        results = [(str(text), float(score)) for text, score in recognizer.rec_res if text]
        return {"text": " ".join(text for text, _ in results),
                "confidence": min((score for _, score in results), default=0),
                "backend": "cix-ppocrv4"}
