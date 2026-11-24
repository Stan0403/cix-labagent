"""Freeze completed user captures for review; never alter the board originals."""

from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import zipfile
from PIL import Image, ImageDraw, ImageOps

PROJECT = Path("/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo")
sys.path.insert(0, str(PROJECT))
from labagent.vision_regions import lcd_region

ROOT = PROJECT / "data/ocr-capture"
OUT = Path("/tmp/codex-ocr-user-captures-" + time.strftime("%Y%m%d-%H%M%S"))
OUT.mkdir(exist_ok=False)
rows = []
overview = []
crops = []
with zipfile.ZipFile(OUT / "captures.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    for batch in sorted(ROOT.iterdir()):
        if (
            not batch.is_dir()
            or batch.is_symlink()
            or not (batch / "batch.json").is_file()
        ):
            continue
        archive.writestr(
            f"batches/{batch.name}/batch.json", (batch / "batch.json").read_bytes()
        )
        records = []
        for path in (batch / "records").glob("*.json"):
            row = json.loads(path.read_text())
            records.append((row["saved_at"], path, row))
        for _, record_path, record in sorted(records):
            image_path = (batch / record["path"]).resolve()
            if (
                not image_path.is_relative_to(batch.resolve())
                or image_path.is_symlink()
            ):
                raise ValueError("Unsafe image path")
            raw = image_path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != record["sha256"]:
                raise ValueError("Capture SHA mismatch")
            idx = len(rows)
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            roi = lcd_region(im)
            row = {
                **record,
                "review_id": idx,
                "source_path": str(image_path),
                "local_source": f"images/frame-{idx:03d}.jpg",
                "source_sha256": digest,
                "roi": roi,
                "snapshot_batch": batch.name,
            }
            archive.writestr(row["local_source"], raw)
            archive.writestr(
                f"records/record-{idx:03d}.json",
                json.dumps(record, ensure_ascii=False, indent=2),
            )
            tile = Image.new("RGB", (300, 205), "white")
            tile.paste(ImageOps.contain(im, (298, 175)), (1, 27))
            ImageDraw.Draw(tile).text(
                (4, 5), f"{idx}: {record['kind']} {record.get('label')}", fill="black"
            )
            overview.append(tile)
            if roi:
                x, y, w, h = roi
                crop = im.crop(
                    (
                        round(x * im.width),
                        round(y * im.height),
                        round((x + w) * im.width),
                        round((y + h) * im.height),
                    )
                )
                b = io.BytesIO()
                crop.save(b, format="PNG")
                row["local_crop"] = f"crops/crop-{idx:03d}.png"
                archive.writestr(row["local_crop"], b.getvalue())
                tile = Image.new("RGB", (260, 165), "#ddd")
                tile.paste(ImageOps.contain(crop, (256, 130)), (2, 30))
                ImageDraw.Draw(tile).text((4, 5), str(idx), fill="black")
                crops.append(tile)
            rows.append(row)
    archive.writestr("inventory.json", json.dumps(rows, ensure_ascii=False, indent=2))
    for name, tiles, columns, perpage in [
        ("overview", overview, 5, 30),
        ("lcd", crops, 5, 30),
    ]:
        for start in range(0, len(tiles), perpage):
            tw, th = tiles[0].size
            page = Image.new(
                "RGB", (tw * columns, th * ((perpage + columns - 1) // columns)), "#bbb"
            )
            for j, tile in enumerate(tiles[start : start + perpage]):
                page.paste(tile, ((j % columns) * tw, (j // columns) * th))
            b = io.BytesIO()
            page.save(b, format="JPEG", quality=95)
            archive.writestr(f"{name}-{start // perpage}.jpg", b.getvalue())
summary = {
    "captures": len(rows),
    "batches": dict(Counter(r["batch_id"] for r in rows)),
    "types": dict(Counter(r["kind"] for r in rows)),
    "provided_labels": dict(Counter(str(r.get("label")) for r in rows)),
    "auto_lcd_crops": len(crops),
    "archive": str(OUT / "captures.zip"),
    "sha256": hashlib.sha256((OUT / "captures.zip").read_bytes()).hexdigest(),
}
(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
Path("/tmp/codex-ocr-latest-capture-export.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2)
)
print(json.dumps(summary, ensure_ascii=False))
