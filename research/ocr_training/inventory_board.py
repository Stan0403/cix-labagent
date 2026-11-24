"""Read-only review of project camera data; never supplies automatic OCR labels."""

import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps

BASE = Path("/home/radxa/referenced-chatgpt-conversation-this-is-an")
OUT = Path("/tmp/codex-ocr-training-inventory")
OUT.mkdir(exist_ok=True)
paths = []
for root in [
    BASE / "LabAgent-demo/data/equipment-capture/raw",
    BASE / "LabAgent-demo/data/images",
    BASE / "LabAgent/data/images",
]:
    if root.exists():
        paths += [
            p for p in root.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")
        ]
seen = set()
records = []
thumbs = []
for path in sorted(paths):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest in seen:
        continue
    seen.add(digest)
    try:
        im = Image.open(path).convert("RGB")
        idx = len(records)
        records.append(
            {
                "id": idx,
                "path": str(path),
                "sha256": digest,
                "size": list(im.size),
                "label": None,
            }
        )
        tile = Image.new("RGB", (240, 170), "white")
        tile.paste(ImageOps.contain(im, (240, 145)), (0, 20))
        ImageDraw.Draw(tile).text((5, 3), str(idx), fill="black")
        thumbs.append(tile)
    except OSError:
        pass
for start in range(0, len(thumbs), 60):
    page = Image.new("RGB", (240 * 6, 170 * 10), "#ccc")
    for j, tile in enumerate(thumbs[start : start + 60]):
        page.paste(tile, ((j % 6) * 240, (j // 6) * 170))
    page.save(OUT / f"overview-{start // 60}.jpg", quality=90)
(OUT / "inventory.json").write_text(json.dumps(records, indent=2))
print(
    json.dumps(
        {
            "unique_images": len(records),
            "pages": (len(thumbs) + 59) // 60,
            "output": str(OUT),
        }
    )
)
