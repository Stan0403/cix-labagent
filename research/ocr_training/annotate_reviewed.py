"""Only labels visually reviewed from LCD contact sheets enter the dataset."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# IDs are from the SHA-deduplicated board inventory, not OCR predictions.
numeric = {
    2: "1.34",
    5: "1.353",
    6: ".419",  # Corrected after magnifying the source; diagnostic only.
    10: "-0.00",
    134: "1.34",
    150: "1.34",
    151: "0.00",
    154: "1.34",
    155: "0.00",
    157: "0.00",
    161: "1.34",
    162: "0.00",
    167: "1.34",
    172: "1.34",
    173: "0.00",
    176: "1.34",
    184: "1.34",
    186: "1.34",
    191: "1.34",
    194: "0.00",
    200: "1.34",
    204: "1.34",
    209: "1.34",
    214: "-0.00",
    216: "1.34",
}
blank = [
    137,
    140,
    141,
    142,
    146,
    153,
    156,
    159,
    166,
    175,
    177,
    178,
    185,
    188,
    190,
    192,
    199,
    205,
    207,
    208,
    210,
    212,
    213,
]
rows = []
for item in json.loads((ROOT / "data/real/candidates.json").read_text()):
    idx = item["id"]
    if idx not in numeric and idx not in blank:
        continue
    label = numeric.get(idx, "")
    rows.append(
        {
            "id": "real-" + str(idx),
            "path": "real/" + item["crop"],
            "label": label,
            "split": "holdout" if idx <= 12 else "train",
            "origin": "camera",
            "group": "legacy-project-camera" if idx <= 12 else "demo-project-camera",
            "source_path": item["path"],
            "source_sha256": item["sha256"],
            "sha256": hashlib.sha256(
                (ROOT / "data/real" / item["crop"]).read_bytes()
            ).hexdigest(),
            "label_method": "manual visual review of original pixels; not model prediction",
            "capture_session_independence": "unverified; same meter and camera; diagnostic only",
        }
    )
(ROOT / "data/real.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
)
print(
    json.dumps(
        {
            "camera_train": sum(x["split"] == "train" for x in rows),
            "camera_holdout": sum(x["split"] == "holdout" for x in rows),
            "excluded_crops": 57 - len(rows),
        }
    )
)
