"""Actual Qwen3-VL endpoint checks against a saved image; never creates a live session."""
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
IMAGE = ROOT.parent/"LabAgent/data/images/82b563446df13fa42a84c4e587c1b1379c84699f36f6011775881f9216d007e8.jpg"
results = []
with httpx.Client(timeout=240, trust_env=False) as client:
    for _ in range(30):
        try:
            if client.get("http://127.0.0.1:8031/health").json().get("ready"):
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    for rule in ("meter_setup", "battery_contact", "battery_cleanup"):
        start = time.monotonic()
        response = client.post("http://127.0.0.1:8031/check_step",
            files={"file": (IMAGE.name, IMAGE.read_bytes(), "image/jpeg")}, data={"rule": rule})
        item = {"rule": rule, "status": response.status_code, "seconds": round(time.monotonic()-start,2), "result": response.json()}
        results.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
        (ROOT/"test-results/workflow-real-model-validation.json").write_text(json.dumps({
            "sample": str(IMAGE), "purpose": "real tool integration, not an accuracy benchmark or live physical experiment", "results": results,
        }, ensure_ascii=False, indent=2))
