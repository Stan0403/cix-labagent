"""Run real local close-up inference on a stored frame, without changing experiments."""

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, choices=range(1, 4), default=2)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LABAGENT_METER_VLM_SERVER", "http://127.0.0.1:8034")
    os.environ.setdefault("LABAGENT_EQUIPMENT_YOLO_SERVER", "http://127.0.0.1:8035")
    spec = importlib.util.spec_from_file_location(
        "closeup_verification_adapter", ROOT / "scripts/qwen3vl_gguf_service.py"
    )
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    from starlette.datastructures import UploadFile

    raw = args.image.read_bytes()
    report = {
        "real_models": True,
        "stored_image_sha256": hashlib.sha256(raw).hexdigest(),
        "physical_task_performed": False,
        "experiment_records_modified": False,
        "semantic_accuracy_verified": False,
        "runs": [],
    }
    try:
        for index in range(args.repeat):
            started = time.perf_counter()
            result = adapter.check_meter_live(UploadFile(file=io.BytesIO(raw), filename="stored-frame.jpg"))
            views = result.pop("detail_views", [])
            for view in views:
                image_url = view.pop("image_url")
                path = args.output / f"run-{index + 1}-{view['part']}.jpg"
                path.write_bytes(base64.b64decode(image_url.split(",", 1)[1]))
                view["file"] = path.name
            result["detail_views"] = views
            result["wall_seconds"] = round(time.perf_counter() - started, 3)
            assert result.get("model_used") and len(views) == 2, "Both close-ups must actually run"
            report["runs"].append(result)
            print("CLOSEUP_REAL_RUN", index + 1, json.dumps(result, ensure_ascii=True), flush=True)
        answers = {
            json.dumps({"checks": run["checks"], "meter_range": run["meter_range"]}, sort_keys=True)
            for run in report["runs"]
        }
        report.update(plumbing_passed=True, repeated_same_image_consistent=len(answers) == 1)
    finally:
        (args.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
