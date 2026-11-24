"""ARM-host collector verification; synthetic photos only enter an owned temporary directory."""

from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import urllib.request
from uuid import uuid4

ROOT = Path("/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo")
sys.path.insert(0, str(ROOT / "scripts"))
from lcd_capture import create_app
from fastapi.testclient import TestClient
from PIL import Image

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://127.0.0.1:8766/health") as response:
    health = json.load(response)
assert health["service"] == "lcd-keyboard-capture"
with opener.open("http://127.0.0.1:8766/") as response:
    assert "空格".encode() in response.read()
with opener.open("http://127.0.0.1:8001/api/health") as response:
    assert json.load(response)["status"] == "ok"
checks = []
with tempfile.TemporaryDirectory(prefix="codex-lcd-capture-test-") as directory:
    with TestClient(create_app(directory)) as client:
        batch = client.post(
            "/api/batches", data={"name": "SYNTHETIC HOST TEST / NOT TRAINING"}
        ).json()["batch_id"]
        buffer = BytesIO()
        Image.new("RGB", (1920, 1080), "#6a9295").save(buffer, "JPEG", quality=98)
        raw = buffer.getvalue()
        for kind, label, expected in [
            ("reading", "1.353", "1.353"),
            ("blank", "stale", ""),
            ("unreadable", "stale", None),
        ]:
            result = client.post(
                f"/api/batches/{batch}/photos",
                data={"capture_id": uuid4().hex, "kind": kind, "label": label},
                files={"file": ("test.jpg", raw, "image/jpeg")},
            )
            assert result.status_code == 201, result.text
            data = result.json()
            assert data["record"]["label"] == expected
            assert (Path(data["directory"]) / data["filename"]).read_bytes() == raw
            checks.append(
                {
                    "kind": kind,
                    "label": expected,
                    "size": [data["record"]["width"], data["record"]["height"]],
                }
            )
report = {
    "passed": True,
    "actual_host": "O6 aarch64",
    "service_health": health,
    "checks": checks,
    "synthetic_test_data": "temporary directory removed; production dataset not used",
    "real_camera_capture": "requires user camera permission on new browser origin; existing Firefox owns camera",
    "desktop_entry": str(Path.home() / "Desktop/labagent-lcd-capture.desktop"),
    "photos_shortcut": str(Path.home() / "Desktop/液晶采集照片"),
}
assert Path(report["desktop_entry"]).is_file()
assert Path(report["photos_shortcut"]).is_symlink()
Path("/tmp/codex-lcd-capture-verification.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(json.dumps(report, ensure_ascii=False))
