"""Small retained-image sanity check against the running OCR; no production experiment writes."""

import json
import math
from pathlib import Path
import sys
import time

import httpx

APP = Path("/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo")
WORK = Path.home() / ".local/state/codex-battery-polarity/20261124"
sys.path.insert(0, str(WORK / "candidate"))
from labagent.battery_polarity import reading_issue
from labagent.readings import parse_candidate

cases = []
plan = {"measurement": {"adapter": "voltage", "polarity_check": "positive"}}
with httpx.Client(trust_env=False, timeout=45) as client:
    for name, expected in [("negative_zero.png", -0.0), ("reading_1_353.png", 1.353)]:
        start = time.perf_counter()
        response = client.post(
            "http://127.0.0.1:8031/recognize",
            files={
                "file": (
                    name,
                    (APP / "tests/fixtures/lcd" / name).read_bytes(),
                    "image/png",
                )
            },
        )
        response.raise_for_status()
        body = response.json()
        parsed = parse_candidate(body["text"], "dc_20v")
        value = parsed["value"]
        matches = (
            value is not None
            and value == expected
            and math.copysign(1, value) == math.copysign(1, expected)
        )
        cases.append(
            {
                "file": name,
                "expected": expected,
                "value": value,
                "matches": matches,
                "backend": body["backend"],
                "fallback_used": body.get("fallback_used"),
                "issue": reading_issue(plan, value),
                "seconds": round(time.perf_counter() - start, 3),
            }
        )
report = {
    "passed": all(c["matches"] for c in cases),
    "retained_images_only": True,
    "independent_accuracy_evaluation": False,
    "production_experiments_written": False,
    "cases": cases,
}
(WORK / "reader-sanity.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(json.dumps(report, ensure_ascii=False))
if not report["passed"]:
    raise SystemExit(1)
