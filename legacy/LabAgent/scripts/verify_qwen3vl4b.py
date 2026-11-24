"""Exercise real local VLM image tools without changing experiment records."""
import argparse
import io
import json
import time
import urllib.request
import uuid
from pathlib import Path

from PIL import Image


def call(base, image, labels, endpoint="inspect"):
    boundary = "labagent-" + uuid.uuid4().hex
    prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"labels\"\r\n\r\n"
              + json.dumps(labels, ensure_ascii=False)
              + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"scene.jpg\"\r\n"
                "Content-Type: image/jpeg\r\n\r\n").encode()
    body = prefix + image + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(base + "/" + endpoint, data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=235) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--base", default="http://127.0.0.1:8033")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(30):
        try:
            with opener.open(args.base.rstrip("/") + "/health", timeout=3) as response:
                if json.load(response).get("ready"):
                    break
        except (OSError, ValueError):
            pass
        time.sleep(1)
    else:
        raise SystemExit("VLM adapter did not become ready")
    blank = io.BytesIO()
    Image.new("RGB", (896, 504), "white").save(blank, format="JPEG")
    real = args.image.read_bytes()
    labels = {"battery": "单节干电池", "multimeter": "数字万用表", "probes": "红黑表笔"}
    split = {"multimeter": "数字万用表", "battery": "干电池",
             "red_probe": "红色万用表表笔", "black_probe": "黑色万用表表笔"}
    records = []
    cases = [(f"scene_{i + 1}", real, labels, "inspect") for i in range(3)]
    cases += [("blank_negative", blank.getvalue(), labels, "inspect"),
              ("scene_separate_probes", real, split, "inspect")]
    # The left crop retains the red handheld probe but excludes the black one.
    missing_black = io.BytesIO()
    with Image.open(args.image) as original:
        original.crop((0, 0, 1080, original.height)).save(missing_black, format="JPEG")
    cases.append(("missing_black_probe_negative", missing_black.getvalue(), labels, "inspect"))
    # This saved photograph has an unlit LCD. This checks rejection, not reading accuracy.
    screen = io.BytesIO()
    with Image.open(args.image) as original:
        original.crop((490, 405, 685, 525)).save(screen, format="JPEG")
    cases.append(("unlit_display_negative", screen.getvalue(), labels, "recognize"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for name, frame, requested, endpoint in cases:
        started = time.monotonic()
        try:
            result = call(args.base.rstrip("/"), frame, requested, endpoint)
        except Exception as exc:
            result = {"error": str(exc)}
        record = {"case": name, "seconds": round(time.monotonic() - started, 2), "result": result}
        records.append(record)
        args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(record, ensure_ascii=False), flush=True)
    if any("error" in record["result"] for record in records):
        raise SystemExit("One or more VLM requests failed; see the output JSON")


if __name__ == "__main__":
    main()
