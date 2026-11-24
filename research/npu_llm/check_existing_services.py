"""Check the original services with a stored frame; never touch experiment DBs."""

import json
import time
from pathlib import Path

import httpx

ROOT = Path("/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo")
report = {"production_experiment_records_modified": False}
with httpx.Client(timeout=20, trust_env=False) as client:
    for port, path in [
        (8081, "/health"),
        (8001, "/api/health"),
        (8031, "/health"),
        (8011, "/health"),
    ]:
        r = client.get(f"http://127.0.0.1:{port}{path}")
        r.raise_for_status()
        report[str(port)] = r.json()
    photo = next(
        (ROOT / "data/ocr-capture/20261122_191101_3fbaeb8d/images").glob("*.jpg")
    )
    start = time.perf_counter()
    r = client.post(
        "http://127.0.0.1:8035/inspect",
        data={
            "labels": json.dumps(
                {"battery": "电池", "multimeter": "万用表", "probes": "红黑表笔"}
            )
        },
        files={"file": (photo.name, photo.read_bytes(), "image/jpeg")},
    )
    r.raise_for_status()
    report["yolo_actual_inference"] = {
        "seconds": time.perf_counter() - start,
        "result": r.json(),
    }
Path("/tmp/codex-qwen3-npu-existing-services.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(
    json.dumps(
        {"existing_services_healthy": True, "yolo": report["yolo_actual_inference"]},
        ensure_ascii=False,
    ),
    flush=True,
)
