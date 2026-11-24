"""Offline float-ONNX diagnostic only; requires no PyTorch or Paddle installation."""

import argparse
import json
import re
import time
import cv2
import onnxruntime as ort
from ocr_common import preprocess, decode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--model", default="ocr_training/runs/lcd-v2/rec-lcd.onnx")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 100:
        raise ValueError("--repeat must be 1..100")
    image = cv2.imread(args.image)
    x = preprocess(image)[None]
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    session = ort.InferenceSession(
        args.model, options, providers=["CPUExecutionProvider"]
    )
    timings = []
    for _ in range(args.repeat):
        start = time.perf_counter()
        probs = session.run(None, {"x": x})[0]
        timings.append(time.perf_counter() - start)
    text = decode(probs)[0]
    print(
        json.dumps(
            {
                "raw_text": text,
                "numeric_syntax_valid": bool(
                    re.fullmatch(r"-?(?:\d+(?:\.\d+)?|\.\d+)", text)
                ),
                "backend": "ONNX Runtime CPU float32; not NPU",
                "accepted_experiment_reading": False,
                "inference_seconds": timings,
                "note": "Syntax validity does not establish a correct reading, range, unit or confidence",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
