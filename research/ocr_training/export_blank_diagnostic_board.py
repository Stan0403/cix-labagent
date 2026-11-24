"""Unlabeled candidate crops reserved solely for additional blank-screen checks."""

import json
import sys
from pathlib import Path
import zipfile
from PIL import Image

sys.path.insert(
    0, "/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo"
)
from labagent.vision_regions import lcd_region

out = Path("/tmp/codex-ocr-blank-diagnostic")
out.mkdir(exist_ok=True)
rows = json.loads(Path("/tmp/codex-ocr-training-inventory/inventory.json").read_text())
items = []
for row in rows:
    if row["id"] not in (249, 253, 261, 278, 293, 325, 338, 341):
        continue
    im = Image.open(row["path"]).convert("RGB")
    roi = lcd_region(im)
    if not roi:
        continue
    x, y, w, h = roi
    im.crop(
        (
            round(x * im.width),
            round(y * im.height),
            round((x + w) * im.width),
            round((y + h) * im.height),
        )
    ).save(out / f"blank-{row['id']}.png")
    items.append({**row, "roi": roi, "label": None, "crop": f"blank-{row['id']}.png"})
(out / "sources.json").write_text(json.dumps(items, indent=2))
with zipfile.ZipFile(
    "/tmp/codex-ocr-blank-diagnostic.zip", "w", zipfile.ZIP_DEFLATED
) as z:
    for p in out.iterdir():
        z.write(p, p.name)
print(json.dumps({"unlabeled_candidates": len(items)}))
