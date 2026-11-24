"""Labels transcribed by visually reviewing all 81 original-resolution LCD crops."""

from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data/user-captures-20261122"
LABELS = [
    "-0.00",
    "1.25",
    "1.05",
    "0.00",
    "0.87",
    "1.04",
    "1.24",
    "0.76",
    "0.88",
    "0.08",
    "1.24",
    "0.86",
    "1.00",
    "-0.00",
    "-0.00",
    "0.06",
    "0.09",
    "0.07",
    "0.01",
    "-0.00",
    "0.08",
    "1.65",
    "1.66",
    "1.65",
    "-0.00",
    "0.00",
    "1.66",
    "1.65",
    "-1.34",
    "-0.99",
    "-1.29",
    "-1.22",
    "-0.27",
    "-1.06",
    "-1.34",
    "-1.34",
    "-0.71",
    "-1.22",
    "-1.34",
    "-1.14",
    "-1.16",
    "-0.69",
    "-1.24",
    "-0.45",
    "-0.08",
    "-0.86",
    "-0.00",
    "-1.25",
    "-1.24",
    "-0.05",
    "-1.24",
    "-1.20",
    "-0.53",
    "-0.01",
    "-0.23",
    "-0.00",
    "0.07",
    "0.02",
    "0.22",
    "0.00",
    "0.00",
    "-0.00",
    "0.98",
    "0.31",
    "1.24",
    "0.57",
    "1.21",
    "0.95",
    "1.25",
    "0.86",
    "0.26",
    "0.86",
    "0.00",
    "1.22",
    "1.25",
    "0.27",
    "0.21",
    "0.07",
    "0.02",
    "0.70",
    "0.20",
]


def main():
    rows = json.loads((DATA / "crop-review.json").read_text(encoding="utf-8"))
    if len(rows) != 81 or len(LABELS) != 81:
        raise ValueError("New photos require new manual review")
    if {r["batch_id"] for r in rows} != {"20261122_191101_3fbaeb8d"}:
        raise ValueError("Wrong reviewed capture batch")
    original = [
        json.loads(line)
        for line in (ROOT / "data/real-v2.jsonl").read_text().splitlines()
        if line
    ]
    additions = []
    for row, label in zip(rows, LABELS):
        if row["review_id"] != len(additions):
            raise ValueError("Review order changed")
        raw = (DATA / row["local_source"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["source_sha256"]:
            raise ValueError("Source pixels changed")
        crop = DATA / row["review_crop"]
        additions.append(
            {
                "id": "capture-" + row["capture_id"],
                "path": crop.relative_to(ROOT / "data").as_posix(),
                "label": label,
                "split": "train",
                "origin": "camera",
                "group": row["batch_id"],
                "source_path": row["source_path"],
                "source_sha256": row["source_sha256"],
                "sha256": hashlib.sha256(crop.read_bytes()).hexdigest(),
                "crop_xyxy": row["crop_xyxy"],
                "capture_id": row["capture_id"],
                "saved_at": row["saved_at"],
                "review_id": row["review_id"],
                "review_status": "visually_reviewed",
                "label_method": "manual visual transcription of displayed digits; no OCR pseudo-labels or voltage inference",
                "limits": "one fixed-camera capture session; numeric strings have two decimal places",
            }
        )
    train = original + additions
    train_hashes = {r["source_sha256"] for r in train if r["split"] == "train"}
    if train_hashes & {r["source_sha256"] for r in train if r["split"] == "holdout"}:
        raise ValueError("Source-image leakage")
    (DATA / "reviewed-labels.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in additions) + "\n",
        encoding="utf-8",
    )
    (ROOT / "data/real-v3.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in train) + "\n",
        encoding="utf-8",
    )
    report = {
        "new_reviewed_training_images": len(additions),
        "train_real_total": sum(r["split"] == "train" for r in train),
        "new_data_batches": 1,
        "new_data_held_out": 0,
        "legacy_numeric_diagnostic": 4,
        "distinct_numeric_strings": len(set(LABELS)),
        "negative_images": sum(s.startswith("-") for s in LABELS),
        "label_frequency": dict(Counter(LABELS)),
        "not_a_test_accuracy": "All 81 new images are training data; their fit is reported separately",
        "crop_locator_note": "Existing production lcd_region failed on all 81 dim images; crops are manually seeded offline annotations, not a deployed detector fix",
    }
    (DATA / "label-review-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
