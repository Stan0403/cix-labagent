"""Probe the activated formal adapter with stored photos; never create experiment records."""

import io
import json
from pathlib import Path
import sys
import time
import urllib.request
import uuid
from PIL import Image

ROOT = Path("/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo")
sys.path.insert(0, str(ROOT))
from labagent.perception import crop_roi
from labagent.readings import parse_candidate

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(route, raw, mime):
    boundary = "formal-" + uuid.uuid4().hex
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="frame"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode()
        + raw
        + f"\r\n--{boundary}--\r\n".encode()
    )
    request = urllib.request.Request(
        "http://127.0.0.1:8031/" + route,
        data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
    )
    start = time.perf_counter()
    with opener.open(request, timeout=120) as response:
        result = json.load(response)
    return result, time.perf_counter() - start


batch = ROOT / "data/ocr-capture/20261122_191101_3fbaeb8d"
rows = [
    json.loads(x)
    for x in (batch / "reviews/manual-review-user81.jsonl").read_text().splitlines()
]
records = []
for idx in [0, 1, 28, 12, 42, 64]:
    row = rows[idx]
    image = Image.open(batch / row["path"]).convert("RGB")
    b = io.BytesIO()
    image.save(b, format="JPEG", quality=85)
    location, t1 = post("locate_display", b.getvalue(), "image/jpeg")
    assert location["backend"] == "opencv-adaptive-lcd-region", location
    crop = crop_roi(image, location["roi"])
    b = io.BytesIO()
    crop.save(b, format="PNG")
    result, t2 = post("recognize", b.getvalue(), "image/png")
    candidate = parse_candidate(result["text"], "dc_20v")
    if candidate["value"] is not None:
        assert abs(candidate["value"] - float(row["label"])) < 1e-8, (row, result)
    records.append(
        {
            "id": idx,
            "truth": row["label"],
            "candidate": candidate,
            "result": result,
            "locate_seconds": t1,
            "recognize_seconds": t2,
            "http_pipeline_seconds": t1 + t2,
        }
    )
    print(
        "frame",
        idx,
        "value",
        candidate["value"],
        "backend",
        result["backend"],
        flush=True,
    )
for idx, truth in [(2, "1.34"), (5, "1.353"), (6, ".419"), (10, "-0.00")]:
    result, seconds = post(
        "recognize",
        Path(f"/tmp/codex-ocr-training-inventory/candidate-{idx:03d}.png").read_bytes(),
        "image/png",
    )
    candidate = parse_candidate(result["text"], "dc_20v")
    if candidate["value"] is not None:
        assert abs(candidate["value"] - float(truth)) < 1e-8, (truth, result)
    records.append(
        {
            "legacy_id": idx,
            "truth": truth,
            "candidate": candidate,
            "result": result,
            "recognize_seconds": seconds,
        }
    )
for idx in [253, 325, 338, 341]:
    result, seconds = post(
        "recognize",
        Path(f"/tmp/codex-ocr-blank-diagnostic/blank-{idx}.png").read_bytes(),
        "image/png",
    )
    assert parse_candidate(result["text"], "dc_20v")["value"] is None
    records.append({"blank_id": idx, "result": result, "recognize_seconds": seconds})
with opener.open("http://127.0.0.1:8031/health") as response:
    health = json.load(response)
assert health["reading_ready"] is True and "ppocr" in health["reading_backend"]
with opener.open("http://127.0.0.1:8001/api/health") as response:
    assert json.load(response)["status"] == "ok"
report = {
    "passed": True,
    "records": records,
    "health": health,
    "production_experiment_records_modified": False,
    "limit": "Stored user training frames and legacy development images used for formal routing checks; not independent accuracy",
}
Path("/tmp/codex-lcd-reader-formal-verification.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(
    json.dumps(
        {
            "passed": True,
            "checks": len(records),
            "reading_backend": health["reading_backend"],
        },
        ensure_ascii=False,
    ),
    flush=True,
)
