"""Fetch public model sources into this isolated training workspace."""

import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)
CIX_REVISION = "f0ee7fef80bd137483da2d988a1c4a2813066036"


def fetch(item):
    name, url = item
    target = ASSETS / name
    if not target.exists():
        temp = target.with_suffix(target.suffix + ".partial")
        with (
            urllib.request.urlopen(url, timeout=120) as response,
            temp.open("wb") as out,
        ):
            while block := response.read(1024 * 1024):
                out.write(block)
        temp.replace(target)
    info = {
        "name": name,
        "url": url,
        "bytes": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    expected = {
        "rec.onnx": "ddc1eb79a3c35d9ef894c877510560d016440bb512d96da3f58d464a65df5063",
        "MODEL-LICENSE.txt": "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
    }
    if name in expected and info["sha256"] != expected[name]:
        raise ValueError(f"Source hash mismatch: {name}")
    print(json.dumps(info), flush=True)
    return info


if __name__ == "__main__":
    sources = [
        (
            "rec.onnx",
            f"https://modelscope.cn/models/cix/ai_model_hub_26_Q2/resolve/{CIX_REVISION}/models/ComputeVision/OCR/onnx_PP_OCRv4/model/rec.onnx",
        ),
        (
            "PaddleOCR-release-2.7.zip",
            "https://codeload.github.com/PaddlePaddle/PaddleOCR/zip/refs/heads/release/2.7",
        ),
        (
            "MODEL-LICENSE.txt",
            f"https://modelscope.cn/models/cix/ai_model_hub_26_Q2/resolve/{CIX_REVISION}/models/ComputeVision/OCR/onnx_PP_OCRv4/model/LICENSE.txt",
        ),
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(fetch, sources))
    (ASSETS / "sources.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
