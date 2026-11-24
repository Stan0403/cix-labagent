"""Deterministic seven-segment LCD samples, explicitly marked synthetic."""

import argparse
import hashlib
import json
from pathlib import Path
import random
import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
SEGMENTS = {
    "0": "abcdef",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgecd",
    "7": "abc",
    "8": "abcdefg",
    "9": "abfgcd",
    "-": "g",
}


def render(text, rng):
    dw = rng.randint(25, 40)
    dh = rng.randint(43, 65)
    height = dh + rng.randint(32, 60)
    thickness = rng.randint(3, 6)
    advance = dw + rng.randint(3, 8)
    width = max(110, int(advance * len(text.replace(".", "")) + rng.randint(70, 100)))
    base = rng.randint(115, 205)
    bg = (
        base - rng.randint(0, 18),
        base + rng.randint(0, 8),
        base + rng.randint(-10, 15),
    )
    im = Image.new("RGB", (width, height), bg)
    d = ImageDraw.Draw(im)
    ink = tuple(rng.randint(5, 55) for _ in range(3))
    x = rng.randint(20, max(21, width - advance * len(text.replace(".", "")) - 25))
    y = rng.randint(16, max(17, height - dh - 16))

    def segment(points):
        # Small polygon cuts preserve seven-segment rather than printed-font shapes.
        d.polygon(points, fill=ink)

    for char in text:
        if char == ".":
            radius = rng.randint(2, 4)
            d.ellipse(
                (x - 3 - radius, y + dh - radius, x - 3 + radius, y + dh + radius),
                fill=ink,
            )
            x += rng.randint(1, 5)
            continue
        t = thickness
        half = dh // 2
        parts = {
            "a": [
                (x + t, y),
                (x + dw - t, y),
                (x + dw - 2 * t, y + t),
                (x + 2 * t, y + t),
            ],
            "g": [
                (x + t, y + half),
                (x + 2 * t, y + half - t // 2),
                (x + dw - 2 * t, y + half - t // 2),
                (x + dw - t, y + half),
                (x + dw - 2 * t, y + half + t // 2),
                (x + 2 * t, y + half + t // 2),
            ],
            "d": [
                (x + t, y + dh),
                (x + dw - t, y + dh),
                (x + dw - 2 * t, y + dh - t),
                (x + 2 * t, y + dh - t),
            ],
            "f": [
                (x, y + t),
                (x + t, y + 2 * t),
                (x + t, y + half - t),
                (x, y + half - 1),
            ],
            "b": [
                (x + dw, y + t),
                (x + dw - t, y + 2 * t),
                (x + dw - t, y + half - t),
                (x + dw, y + half - 1),
            ],
            "e": [
                (x, y + half + 1),
                (x + t, y + half + t),
                (x + t, y + dh - 2 * t),
                (x, y + dh - t),
            ],
            "c": [
                (x + dw, y + half + 1),
                (x + dw - t, y + half + t),
                (x + dw - t, y + dh - 2 * t),
                (x + dw, y + dh - t),
            ],
        }
        for key in SEGMENTS[char]:
            segment(parts[key])
        x += advance
    if rng.random() < 0.65:
        d.rectangle(
            (0, 0, width - 1, height - 1),
            outline=tuple(max(0, v - 70) for v in bg),
            width=rng.randint(2, 6),
        )
    if rng.random() < 0.3:
        d.line((0, height - 5, width, height - 5), fill=(40, 45, 50), width=3)
    array = np.asarray(im).copy()
    # Perspective/rotation and low resolution reproduce camera LCD, without changing labels.
    corners = np.float32(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]]
    )
    delta = np.array(
        [
            [rng.uniform(-0.04, 0.04) * width, rng.uniform(-0.06, 0.06) * height]
            for _ in range(4)
        ],
        np.float32,
    )
    matrix = cv2.getPerspectiveTransform(corners, corners + delta)
    array = cv2.warpPerspective(
        array, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE
    )
    angle = rng.uniform(-11, 11)
    radians = abs(angle) * np.pi / 180
    fit = min(
        width / (width * np.cos(radians) + height * np.sin(radians)),
        height / (height * np.cos(radians) + width * np.sin(radians)),
    )
    rot = cv2.getRotationMatrix2D((width / 2, height / 2), angle, fit * 0.96)
    array = cv2.warpAffine(array, rot, (width, height), borderValue=bg)
    if rng.random() < 0.65:
        factor = rng.uniform(0.45, 0.95)
        array = cv2.resize(
            cv2.resize(array, None, fx=factor, fy=factor), (width, height)
        )
    noise = np.random.default_rng(rng.randrange(2**32)).normal(
        0, rng.uniform(0, 3), array.shape
    )
    array = np.uint8(np.clip(array + noise, 0, 255))
    if rng.random() < 0.5:
        array = cv2.GaussianBlur(array, (3, 3), rng.uniform(0.2, 0.7))
    return array[:, :, ::-1].copy()


def make_label(rng):
    if rng.random() < 0.025:
        return ""
    count = rng.choice([3, 4, 4, 4, 5])
    digits = "".join(str(rng.randrange(10)) for _ in range(count))
    if rng.random() < 0.9:
        point = rng.randrange(1, len(digits))
        digits = digits[:point] + "." + digits[point:]
    return ("-" if rng.random() < 0.3 else "") + digits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=int, default=4000)
    parser.add_argument("--val", type=int, default=400)
    args = parser.parse_args()
    out = ROOT / "data/synthetic"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for split, count, seed in [
        ("train", args.train, 2026112201),
        ("validation", args.val, 2026112202),
    ]:
        rng = random.Random(seed)
        for index in range(count):
            label = make_label(rng)
            # Blank/short strings can repeat; all image seeds and files are disjoint.
            name = f"{split}-{index:05d}.png"
            image = render(label, rng)
            cv2.imwrite(str(out / name), image)
            rows.append(
                {
                    "path": "synthetic/" + name,
                    "label": label,
                    "split": split,
                    "origin": "synthetic",
                    "group": split + "-seed-" + str(seed),
                    "sha256": hashlib.sha256((out / name).read_bytes()).hexdigest(),
                }
            )
    (ROOT / "data/synthetic.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    page = np.full((600, 1000, 3), 230, np.uint8)
    for index, row in enumerate(rows[:20]):
        im = cv2.imread(str(ROOT / "data" / row["path"]))
        im = cv2.resize(im, (240, 90))
        x, y = index % 4 * 250, index // 4 * 120
        page[y + 25 : y + 115, x : x + 240] = im
        cv2.putText(
            page,
            row["label"] or "<blank>",
            (x + 4, y + 19),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 0, 0),
            1,
        )
    cv2.imwrite(str(ROOT / "data/synthetic-preview.jpg"), page)
    print(
        json.dumps(
            {
                "synthetic_train": args.train,
                "synthetic_validation": args.val,
                "labels": "renderer ground truth; not camera measurements",
            }
        )
    )


if __name__ == "__main__":
    main()
