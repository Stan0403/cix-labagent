"""Offline, visually seeded LCD crop candidates for the user's Nov 22 batch.

The manually reviewed search box is dataset annotation, not a production ROI.
No model predictions are used as ground truth.
"""

import json
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parent / "data/user-captures-20261122"
rows = json.loads((ROOT / "inventory.json").read_text(encoding="utf-8"))
(ROOT / "review-crops").mkdir(exist_ok=True)
tiles = []
candidates = []
for row in rows:
    image = cv2.imread(str(ROOT / row["local_source"]))
    # This batch was visually checked to use the same fixed camera/meter placement.
    sx, sy, ex, ey = 840, 360, 1220, 600
    search = image[sy:ey, sx:ex]
    hsv = cv2.cvtColor(search, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv, np.array([75, 25, 48], np.uint8), np.array([104, 145, 160], np.uint8)
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if 1.3 < w / h < 3 and w > 140 and h > 65 and cv2.contourArea(contour) > 6000:
            regions.append(
                (
                    cv2.contourArea(contour),
                    (sx + x - 5, sy + y - 5, sx + x + w + 5, sy + y + h + 5),
                )
            )
    if not regions:
        raise ValueError(f"No reviewed candidate for {row['review_id']}")
    box = max(regions)[1]
    left, top, right, bottom = box
    crop = image[top:bottom, left:right]
    name = f"review-crops/crop-{row['review_id']:03d}.png"
    cv2.imwrite(str(ROOT / name), crop)
    candidates.append(
        {
            **row,
            "review_crop": name,
            "crop_xyxy": box,
            "crop_method": "manually seeded color-region proposal; visual audit required",
        }
    )
    im = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
    tile = Image.new("RGB", (300, 175), "#ddd")
    tile.paste(ImageOps.contain(im, (296, 140)), (2, 30))
    ImageDraw.Draw(tile).text((4, 4), str(row["review_id"]), fill="black")
    tiles.append(tile)
for start in range(0, len(tiles), 30):
    page = Image.new("RGB", (5 * 300, 6 * 175), "#bbb")
    for i, tile in enumerate(tiles[start : start + 30]):
        page.paste(tile, ((i % 5) * 300, (i // 5) * 175))
    page.save(ROOT / f"review-lcd-{start // 30}.jpg", quality=98)
(ROOT / "crop-review.json").write_text(
    json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {
            "candidate_crops": len(candidates),
            "size_range": [
                min(x["crop_xyxy"][2] - x["crop_xyxy"][0] for x in candidates),
                max(x["crop_xyxy"][2] - x["crop_xyxy"][0] for x in candidates),
            ],
        }
    )
)
