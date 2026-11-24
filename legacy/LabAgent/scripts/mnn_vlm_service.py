"""Local, human-verified meter reading preview using the CIX-listed MNN VLM."""
import io
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import MNN.cv as cv
import MNN.llm as llm
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

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
    return {"ready": "model" in STATE, "backend": "Qwen2.5-VL-3B MNN CPU"}


@app.post("/recognize")
async def recognize(file: UploadFile = File(...)):
    data = await file.read(8_000_001)
    if len(data) > 8_000_000:
        raise HTTPException(413, "图片过大")
    try:
        with Image.open(io.BytesIO(data)) as original:
            if original.format not in {"JPEG", "PNG", "WEBP"} or original.width * original.height > 20_000_000:
                raise ValueError()
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((1600, 1600))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(400, "图片无效") from exc
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "display.png"
        image.save(path)
        with MODEL_LOCK:
            model = STATE["model"]
            model.reset()
            var = cv.imread(str(path))
            prompt = {"text": "<|im_start|>user\n<img>image_0</img>Describe this image accurately."
                      "<|im_end|>\n<|im_start|>assistant\n",
                      "images": [{"data": var, "height": image.height, "width": image.width}]}
            text = model.response(prompt, False)
    if not isinstance(text, str):
        raise HTTPException(503, "视觉模型未返回有效文本")
    # VLM confidence is not calibrated. The response is a preview only and
    # cannot create a measurement without a separate human entry.
    return {"text": text.strip()[:1000], "confidence": 0, "backend": "qwen25-vl-mnn-cpu"}
