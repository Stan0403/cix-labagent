"""Activate the authorized trained reader; keep timestamped rollback files."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
import zipfile

ROOT = Path("/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo")
MODEL = ROOT / "data/ocr-training/20261122-user81"
SHA = "ec0b3f0b6090c41720d1bcccc940103fffce74b6db5d1aa044229d40e8655d35"
EXPECTED = {
    "scripts/qwen3vl_gguf_service.py": "6efb82969238b1bdac83b9f4941f54a7bc545fa444a1ba9aca39d5ad532894be",
    "labagent/readings.py": "f9f5407a8ff372d767023487ff38697088b800d914be07ba8e66ed6e2a881e79",
}
FILES = [
    "scripts/qwen3vl_gguf_service.py",
    "labagent/lcd_reader.py",
    "labagent/readings.py",
]
STATE = Path.home() / ".local/state/codex-lcd-reader" / time.strftime("%Y%m%d-%H%M%S")
STATE.mkdir(parents=True, exist_ok=False)
native = json.loads(Path("/tmp/codex-lcd-reader-native/report.json").read_text())
assert (
    native["model_sha256"] == SHA
    and not native["fast_wrong"]
    and native["fast_accepted"] >= 70
)
assert hashlib.sha256((MODEL / "rec-lcd.onnx").read_bytes()).hexdigest() == SHA
for name, digest in EXPECTED.items():
    if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
        raise ValueError("Remote source changed: " + name)
changes = []


def write(target, data):
    index = len(changes)
    before = target.exists()
    backup = STATE / f"backup-{index}"
    if before:
        shutil.copy2(target, backup)
    changes.append(
        {
            "path": str(target),
            "existed": before,
            "backup": str(backup) if before else None,
        }
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name("." + target.name + ".stage")
    temp.write_bytes(data)
    temp.replace(target)


def rollback():
    for item in reversed(changes):
        target = Path(item["path"])
        if item["existed"]:
            shutil.copy2(item["backup"], target)
        else:
            target.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        [
            "systemctl",
            "--user",
            "restart",
            "labagent-demo-vlm.service",
            "labagent-demo-web.service",
        ],
        check=True,
    )


opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wait_json(url, check):
    last = ""
    for _ in range(45):
        try:
            with opener.open(url, timeout=3) as response:
                data = json.load(response)
            if check(data):
                return data
            last = str(data)
        except (OSError, ValueError) as exc:
            last = str(exc)
        time.sleep(1)
    raise RuntimeError("Health check failed: " + last)


try:
    with zipfile.ZipFile("/tmp/codex-lcd-reader-deploy.zip") as archive:
        assert sorted(archive.namelist()) == sorted(FILES)
        for name in FILES:
            write(ROOT / name, archive.read(name))
    dropin = (
        Path.home()
        / ".config/systemd/user/labagent-demo-vlm.service.d/30-lcd-reader.conf"
    )
    config = f"""[Service]
Environment=LABAGENT_LCD_OCR_MODEL={MODEL}/rec-lcd.onnx
Environment=LABAGENT_LCD_OCR_DICT={MODEL}/ppocr_keys_v1.txt
Environment=LABAGENT_LCD_OCR_SHA256={SHA}
Environment=LABAGENT_LCD_OCR_THREADS=4
"""
    write(dropin, config.encode())
    (STATE / "rollback.json").write_text(json.dumps(changes, indent=2))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "--user", "restart", "labagent-demo-vlm.service"], check=True
    )
    adapter = wait_json(
        "http://127.0.0.1:8031/health",
        lambda r: (
            r.get("reading_ready") is True
            and r.get("reading_model_sha256") == SHA
            and r.get("ready") is True
        ),
    )
    subprocess.run(
        ["systemctl", "--user", "restart", "labagent-demo-web.service"], check=True
    )
    web = wait_json(
        "http://127.0.0.1:8001/api/health", lambda r: r.get("status") == "ok"
    )
    report = {
        "status": "activated",
        "state_directory": str(STATE),
        "model_sha256": SHA,
        "adapter_health": adapter,
        "web_health": web,
        "files": changes,
        "production_experiment_records_modified": False,
        "restarted_services": [
            "labagent-demo-vlm.service",
            "labagent-demo-web.service",
        ],
        "NPU_used_for_new_reader": False,
    }
    (STATE / "deployment.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    Path("/tmp/codex-lcd-reader-deployment.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
except Exception:
    rollback()
    raise
