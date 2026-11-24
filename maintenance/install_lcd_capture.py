"""Install only the isolated collector and its desktop entry, backing up changed files."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import zipfile

PROJECT = Path("/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo")
ARCHIVE = Path("/tmp/codex-lcd-capture.zip")
FILES = [
    "scripts/lcd_capture.py",
    "scripts/open_lcd_capture.sh",
    "labagent/web/lcd-capture.html",
    "labagent/web/lcd-capture.js",
    "labagent/web/lcd-capture.css",
    "docs/LCD_CAPTURE.md",
]
STATE = Path.home() / ".local/state/codex-lcd-capture" / time.strftime("%Y%m%d-%H%M%S")
STATE.mkdir(parents=True, exist_ok=False)
changes = []


def install(target, data, mode=0o644):
    before = None
    if target.exists():
        before = hashlib.sha256(target.read_bytes()).hexdigest()
        if target.read_bytes() != data:
            backup = STATE / "backup" / target.relative_to(Path.home())
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name("." + target.name + ".install")
    temporary.write_bytes(data)
    os.chmod(temporary, mode)
    temporary.replace(target)
    changes.append(
        {
            "path": str(target),
            "before_sha256": before,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    )


with zipfile.ZipFile(ARCHIVE) as archive:
    if sorted(archive.namelist()) != sorted(FILES):
        raise ValueError("Unexpected package members")
    for name in FILES:
        install(
            PROJECT / name, archive.read(name), 0o755 if name.endswith(".sh") else 0o644
        )

unit = f"""[Unit]
Description=Local LCD keyboard photo collection
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory={PROJECT}
ExecStart={PROJECT}/.venv-app/bin/python {PROJECT}/scripts/lcd_capture.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""
install(
    Path.home() / ".config/systemd/user/labagent-lcd-capture.service", unit.encode()
)
entry = f"""[Desktop Entry]
Type=Application
Name=液晶数据采集
GenericName=LCD Photo Capture
Comment=打开预览后按空格拍照，保存液晶 OCR 训练图片
Exec=/bin/bash {PROJECT}/scripts/open_lcd_capture.sh
Icon=camera-photo
Terminal=false
Categories=Utility;
StartupNotify=false
"""
install(
    Path.home() / ".local/share/applications/labagent-lcd-capture.desktop",
    entry.encode(),
    0o755,
)
try:
    desktop = Path(
        subprocess.check_output(["xdg-user-dir", "DESKTOP"], text=True).strip()
    )
except (OSError, subprocess.CalledProcessError):
    desktop = Path.home() / "Desktop"
if not desktop.is_relative_to(Path.home()):
    raise ValueError("Unexpected desktop directory")
desktop_file = desktop / "labagent-lcd-capture.desktop"
install(desktop_file, entry.encode(), 0o755)
(PROJECT / "data/ocr-capture").mkdir(parents=True, exist_ok=True)
photos_link = desktop / "液晶采集照片"
if not photos_link.exists() and not photos_link.is_symlink():
    photos_link.symlink_to(PROJECT / "data/ocr-capture", target_is_directory=True)
subprocess.run(
    ["gio", "set", str(desktop_file), "metadata::trusted", "true"], capture_output=True
)
subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
subprocess.run(
    ["systemctl", "--user", "start", "labagent-lcd-capture.service"], check=True
)
report = {
    "files": changes,
    "desktop": str(desktop_file),
    "photos_shortcut": str(photos_link),
    "url": "http://127.0.0.1:8766",
    "output": str(PROJECT / "data/ocr-capture"),
    "auto_start_on_login": False,
    "formal_services_restarted": False,
    "state_directory": str(STATE),
}
(STATE / "installation.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(json.dumps(report, ensure_ascii=False), flush=True)
