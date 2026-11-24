"""Produce unlabeled LCD candidates using the project's existing ROI locator."""

import io
import json
from pathlib import Path
import zipfile
import sys
from PIL import Image, ImageDraw, ImageOps

sys.path.insert(
    0, "/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo"
)
from labagent.vision_regions import lcd_region

OUT = Path("/tmp/codex-ocr-training-inventory")
rows = json.loads((OUT / "inventory.json").read_text())
results = []
for item in rows:
    if item["id"] > 12 and not 134 <= item["id"] <= 223:
        continue
    raw = Path(item["path"]).read_bytes()
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    roi = lcd_region(im)
    if not roi:
        continue
    x, y, w, h = roi
    crop = im.crop(
        (
            round(x * im.width),
            round(y * im.height),
            round((x + w) * im.width),
            round((y + h) * im.height),
        )
    )
    if min(crop.size) < 15:
        continue
    name = f"candidate-{item['id']:03d}.png"
    crop.save(OUT / name)
    results.append(
        {
            **item,
            "roi": roi,
            "crop": name,
            "label": None,
            "split": "holdout" if item["id"] <= 12 else "unassigned",
        }
    )
(OUT / "candidates.json").write_text(json.dumps(results, indent=2))
for start in range(0, len(results), 48):
    page = Image.new("RGB", (6 * 220, 8 * 130), "white")
    for j, item in enumerate(results[start : start + 48]):
        im = Image.open(OUT / item["crop"])
        tile = Image.new("RGB", (220, 130), "#ddd")
        tile.paste(ImageOps.contain(im, (216, 105)), (2, 22))
        ImageDraw.Draw(tile).text((3, 3), str(item["id"]), fill="black")
        page.paste(tile, ((j % 6) * 220, (j // 6) * 130))
    page.save(OUT / f"lcd-{start // 48}.jpg", quality=95)
with zipfile.ZipFile(OUT / "candidates.zip", "w", zipfile.ZIP_DEFLATED) as z:
    z.write(OUT / "candidates.json", "candidates.json")
    for item in results:
        z.write(OUT / item["crop"], item["crop"])
    for p in OUT.glob("lcd-*.jpg"):
        z.write(p, p.name)
print(json.dumps({"candidates": len(results), "zip": str(OUT / "candidates.zip")}))
