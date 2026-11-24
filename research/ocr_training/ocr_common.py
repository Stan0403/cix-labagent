"""Original CIX service preprocessing and CTC decoding, without a training runtime."""

from pathlib import Path
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent


def preprocess(image):
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Expected a readable BGR LCD image")
    h, w = image.shape[:2]
    width = min(400, int(np.ceil(w * 32 / h)))
    resized = cv2.resize(image, (width, 32)).astype(np.float32) / 127.5 - 1
    out = np.zeros((3, 32, 400), np.float32)
    out[:, :, :width] = resized.transpose(2, 0, 1)
    return out


def characters():
    return (
        [""]
        + (ROOT / "assets/ppocr_keys_v1.txt").read_text(encoding="utf-8").splitlines()
        + [" "]
    )


def decode(logits):
    vocab = characters()
    if logits.ndim != 3 or logits.shape[2] != len(vocab):
        raise ValueError(
            "Recognizer output and original character dictionary do not match"
        )
    results = []
    for row in logits.argmax(axis=-1):
        results.append(
            "".join(
                vocab[int(i)]
                for j, i in enumerate(row)
                if i and (j == 0 or i != row[j - 1])
            )
        )
    return results
