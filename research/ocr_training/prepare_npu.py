"""Prepare CIX recompilation inputs; this command does NOT claim NPU deployment."""

import argparse
import hashlib
import json
import random
import cv2
import numpy as np
import onnx
from ocr_common import ROOT, preprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="runs/lcd-v1")
    args = parser.parse_args()
    out = ROOT / args.run
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    if not report["export"]["passed"]:
        raise ValueError("Export parity required")
    graph = onnx.load(str(out / "rec-lcd.onnx"))
    baseline = onnx.load(str(ROOT / "assets/rec.onnx"))
    if [(n.op_type, list(n.input), list(n.output)) for n in graph.graph.node] != [
        (n.op_type, list(n.input), list(n.output)) for n in baseline.graph.node
    ]:
        raise ValueError(
            "Operator topology changed; original CIX profile cannot be assumed"
        )
    rows = []
    for file in [
        report["configuration"].get("real_manifest", "real.jsonl"),
        "synthetic.jsonl",
    ]:
        rows.extend(
            json.loads(line)
            for line in (ROOT / "data" / file).read_text(encoding="utf-8").splitlines()
        )
    real = [r for r in rows if r["origin"] == "camera" and r["split"] == "train"]
    synthetic = [
        r for r in rows if r["origin"] == "synthetic" and r["split"] == "train"
    ]
    chosen = real + random.Random(20261122).sample(synthetic, 64)
    calibration = np.stack(
        [preprocess(cv2.imread(str(ROOT / "data" / r["path"]))) for r in chosen]
    )
    np.save(out / "rec_cal.npy", calibration)
    cfg = (ROOT / "assets/cfg_recbuild.cfg").read_text(encoding="utf-8")
    cfg = cfg.replace(
        "input_model = model/rec.onnx", f"input_model = {args.run}/rec-lcd.onnx"
    )
    cfg = cfg.replace(
        "calibration_data = datasets/rec_cal.npy",
        f"calibration_data = {args.run}/rec_cal.npy",
    )
    cfg = cfg.replace("output_dir = ./out", f"output_dir = {args.run}/cix-out")
    cfg = cfg.replace("outputs = rec.cix", "outputs = rec-lcd.cix")
    if "model/rec.onnx" in cfg or "datasets/rec_cal.npy" in cfg:
        # Config spacing may differ across catalog revisions; fail instead of compiling old weights.
        raise ValueError("Unreplaced model/calibration path in compile config")
    (out / "recbuild.cfg").write_text(cfg, encoding="utf-8")
    metadata = {
        "status": "ready_for_cixbuild_not_compiled",
        "working_directory": str(ROOT),
        "command": f"cixbuild {args.run}/recbuild.cfg",
        "target": "X2_1204MP3",
        "operator_topology_identical": True,
        "input": [1, 3, 32, 400],
        "calibration_shape": list(calibration.shape),
        "calibration_split": "training only",
        "calibration_images": [r["path"] for r in chosen],
        "calibration_sha256": hashlib.sha256(
            (out / "rec_cal.npy").read_bytes()
        ).hexdigest(),
        "required_next_checks": [
            "compile with original CIX SDK",
            "quantized NPU versus float ONNX string/sign/decimal accuracy on held-out camera data",
            "cold/warm latency and repeated blank-screen rejection",
            "keep VLM fallback until real-world acceptance criteria are met",
        ],
    }
    (out / "npu-preparation.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in metadata.items() if k != "calibration_images"}))


if __name__ == "__main__":
    main()
