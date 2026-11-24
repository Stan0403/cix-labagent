"""Evaluate manually reviewed, never-trained blank LCD crops separately from digits."""

import argparse
import hashlib
import json
import cv2
import onnxruntime as ort
from ocr_common import ROOT, preprocess, decode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="runs/lcd-v2")
    args = parser.parse_args()
    out = ROOT / args.run
    source = ROOT / "data/blank-diagnostic"
    rows = json.loads((source / "sources.json").read_text())
    if {row["id"] for row in rows} != {253, 325, 338, 341}:
        raise ValueError("Blank candidates changed; new manual label review required")
    training = json.loads((out / "config.json").read_text())
    manifest = training.get("real_manifest", "real.jsonl")
    train = [
        json.loads(x) for x in (ROOT / "data" / manifest).read_text().splitlines() if x
    ]
    train_hashes = {row["source_sha256"] for row in train if row["split"] == "train"}
    if train_hashes & {row["sha256"] for row in rows}:
        raise ValueError("Blank diagnostic original image was used in training")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    sessions = {
        name: ort.InferenceSession(
            str(path), options, providers=["CPUExecutionProvider"]
        )
        for name, path in [
            ("original", ROOT / "assets/rec.onnx"),
            ("trained", out / "rec-lcd.onnx"),
        ]
    }
    records = []
    for row in rows:
        x = preprocess(cv2.imread(str(source / row["crop"])))[None]
        record = {
            "id": row["id"],
            "truth": "",
            "source_path": row["path"],
            "source_sha256": row["sha256"],
            "label_method": "manual visual review; no large LCD digits visible",
            "split": "diagnostic_only_never_train_or_calibrate",
        }
        for name, session in sessions.items():
            record[name] = decode(session.run(None, {"x": x})[0])[0]
        records.append(record)
    result = {
        "count": len(rows),
        "original_blank_correct": sum(r["original"] == "" for r in records),
        "trained_blank_correct": sum(r["trained"] == "" for r in records),
        "records": records,
        "limit": "Same meter/camera and possibly same capture session; four diagnostic blanks only",
        "model_sha256": hashlib.sha256((out / "rec-lcd.onnx").read_bytes()).hexdigest(),
    }
    (out / "blank-diagnostic.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
