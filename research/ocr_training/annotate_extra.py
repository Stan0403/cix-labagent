"""Four additional LCD crops; labels preserve visible dots/leading zeros literally."""

import hashlib
import json
from PIL import Image
from ocr_common import ROOT

boxes = {
    223: (917, 410, 1144, 534),
    232: (714, 324, 925, 458),
    290: (1027, 378, 1204, 478),
    324: (978, 337, 1215, 470),
}
# In particular, do NOT infer 1.359, 0.03 or 0.01 from the experimental context.
labels = {223: "1359", 232: "0.89", 290: "00.3", 324: ".001"}
rows = [json.loads(x) for x in (ROOT / "data/real.jsonl").read_text().splitlines()]
sources = json.loads((ROOT / "data/extra/sources.json").read_text())
for source in sources:
    idx = source["id"]
    name = f"extra/crop-{idx}.png"
    Image.open(ROOT / f"data/extra/scene-{idx}.jpg").crop(boxes[idx]).save(
        ROOT / "data" / name
    )
    rows.append(
        {
            "id": "real-" + str(idx),
            "path": name,
            "label": labels[idx],
            "split": "train",
            "origin": "camera",
            "group": "demo-project-camera",
            "source_path": source["path"],
            "source_sha256": source["sha256"],
            "crop_xyxy": boxes[idx],
            "sha256": hashlib.sha256((ROOT / "data" / name).read_bytes()).hexdigest(),
            "label_method": "manual visual review of full-resolution LCD crop; no range/unit inference",
            "capture_session_independence": "unverified; same meter/camera; diagnostic only",
        }
    )
(ROOT / "data/real-v2.jsonl").write_text(
    "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
)
print(
    json.dumps(
        {"camera_train": sum(x["split"] == "train" for x in rows), "camera_holdout": 4}
    )
)
