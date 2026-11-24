"""Read-only real-model check against an existing, explicitly identified sample image."""
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
IMAGE = ROOT.parent / "LabAgent/data/images/82b563446df13fa42a84c4e587c1b1379c84699f36f6011775881f9216d007e8.jpg"
with httpx.Client(timeout=600, trust_env=False) as client:
    for _ in range(30):
        try:
            if client.get("http://127.0.0.1:8031/health").json().get("ready"):
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    start = time.monotonic()
    response = client.post("http://127.0.0.1:8031/inspect",
        files={"file": (IMAGE.name, IMAGE.read_bytes(), "image/jpeg")},
        data={"labels": json.dumps({"battery": "电池", "multimeter": "万用表", "probes": "红黑表笔"}, ensure_ascii=False)})
    result = {"sample": IMAGE.name, "seconds": round(time.monotonic()-start, 2), "status": response.status_code,
              "response": response.json()}
    (ROOT / ".runtime/direct-vision-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)
