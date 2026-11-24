"""Manage an independent local-model stack; the original demo remains on port 8000."""
import argparse
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORIGINAL = ROOT.parent / "LabAgent"
STATE = ROOT / ".runtime"
SERVICES = ["labagent-demo-llm", "labagent-demo-asr", "labagent-demo-vlm", "labagent-demo-web"]


def prepare():
    STATE.mkdir(exist_ok=True, mode=0o700)
    models = ROOT / "models"
    if not models.exists():
        models.symlink_to(ORIGINAL / "models", target_is_directory=True)
    requirements = [ROOT / ".venv-app/bin/python", ORIGINAL / ".venv-asr/bin/python", ORIGINAL / ".venv-vlm/bin/python",
                    models / "qwen3-4b/Qwen3-4B-Q4_K_M.gguf", models / "qwen25vl-mnn/config.json", models / "sensevoice-cix/sense_voice_mask256.cix"]
    for path in requirements:
        if not path.exists():
            raise RuntimeError("缺少已部署依赖：" + str(path))
    environment = """LABAGENT_HOST=127.0.0.1
LABAGENT_PORT=8001
LABAGENT_LOCAL_ONLY=true
LABAGENT_EVIDENCE_TTL_SECONDS=900
LABAGENT_HTTP_TIMEOUT=240
LABAGENT_CHAT_PROVIDER=api
LABAGENT_LLM_BASE_URL=http://127.0.0.1:8081/v1
LABAGENT_LLM_MODEL=qwen3-4b-local
LABAGENT_LLM_API_KEY=
LABAGENT_ASR_BASE_URL=http://127.0.0.1:8011/v1
LABAGENT_ASR_MODEL=sensevoice-small-cix
LABAGENT_ASR_API_KEY=
LABAGENT_OCR_PROVIDER=mnn
LABAGENT_OCR_BASE_URL=http://127.0.0.1:8031
LABAGENT_VISION_PROVIDER=disabled
"""
    path = ROOT / ".env"
    if not path.exists():
        path.write_text(environment, encoding="utf-8")
        path.chmod(0o600)


def launch(name, args, env=None):
    with (STATE / (name + ".log")).open("ab") as log:
        p = subprocess.Popen(args, cwd=ROOT, env={**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1"},
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (STATE / (name + ".pid")).write_text(str(p.pid))
    print(name, "pid", p.pid)


def stop_preview():
    for name in ["preview-web", "preview-vlm"]:
        file = STATE / (name + ".pid")
        if file.exists():
            pid = int(file.read_text())
            try:
                command = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
                cwd = Path(f"/proc/{pid}/cwd").resolve()
                if cwd == ROOT and ("labagent" in command or "mnn_vlm_service" in command):
                    os.killpg(pid, signal.SIGTERM)
                    deadline = time.monotonic() + 7
                    while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
                        time.sleep(.15)
                    if Path(f"/proc/{pid}").exists():
                        os.killpg(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            file.unlink(missing_ok=True)


def preview():
    prepare()
    stop_preview()
    launch("preview-vlm", [str(ORIGINAL / ".venv-vlm/bin/python"), "-m", "uvicorn", "scripts.mnn_vlm_service:app", "--host", "127.0.0.1", "--port", "8031"])
    launch("preview-web", [str(ROOT / ".venv-app/bin/python"), "-m", "labagent"],
           {"LABAGENT_PORT": "8001", "LABAGENT_OCR_BASE_URL": "http://127.0.0.1:8031",
            "LABAGENT_LLM_BASE_URL": "http://127.0.0.1:8080/v1", "LABAGENT_ASR_BASE_URL": "http://127.0.0.1:8010/v1"})


def activate():
    prepare()
    path = ROOT / ".env"
    settings = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep:
            settings[key] = value
    settings.update(LABAGENT_PORT="8001", LABAGENT_LOCAL_ONLY="true",
                    LABAGENT_LLM_BASE_URL="http://127.0.0.1:8081/v1", LABAGENT_ASR_BASE_URL="http://127.0.0.1:8011/v1",
                    LABAGENT_OCR_BASE_URL="http://127.0.0.1:8031")
    path.write_text("\n".join(f"{k}={v}" for k,v in settings.items())+"\n")
    stop_preview()
    subprocess.run(["systemctl", "--user", "stop", "labagent-demo.target", *[s + ".service" for s in SERVICES]], check=False, capture_output=True)
    time.sleep(2)
    unit_dir = Path.home() / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    commands = {
        "llm": f"/bin/bash {ROOT}/scripts/start_local_llm.sh",
        "asr": f"{ORIGINAL}/.venv-asr/bin/python -m labagent.speech_service",
        "vlm": f"{ORIGINAL}/.venv-vlm/bin/python -m uvicorn scripts.mnn_vlm_service:app --host 127.0.0.1 --port 8031",
        "web": f"{ROOT}/.venv-app/bin/python -m labagent",
    }
    for name, command in commands.items():
        extra = "Wants=labagent-demo-llm.service labagent-demo-asr.service labagent-demo-vlm.service\nAfter=labagent-demo-llm.service labagent-demo-asr.service labagent-demo-vlm.service\n" if name == "web" else ""
        environment = "Environment=LABAGENT_LLM_PORT=8081\n" if name == "llm" else "Environment=LABAGENT_LOCAL_ASR_PORT=8011\n" if name == "asr" else ""
        content = (f"[Unit]\nDescription=LabAgent O6 {name}\nPartOf=labagent-demo.target\n" + extra
                   + f"[Service]\nType=simple\nWorkingDirectory={ROOT}\nEnvironment=PYTHONUNBUFFERED=1\n" + environment + f"ExecStart={command}\n"
                   + "Restart=on-failure\nRestartSec=5\nTimeoutStopSec=20\n\n[Install]\nWantedBy=labagent-demo.target\n")
        (unit_dir / f"labagent-demo-{name}.service").write_text(content)
    (unit_dir / "labagent-demo.target").write_text("[Unit]\nDescription=LabAgent O6 demonstration\nWants=" + " ".join(s + ".service" for s in SERVICES) + "\n\n[Install]\nWantedBy=default.target\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "labagent-demo.target"], check=True)
    print("DEMO_TARGET_ENABLED")


def rollback():
    stop_preview()
    subprocess.run(["systemctl", "--user", "disable", "--now", "labagent-demo.target"], check=False)
    subprocess.run(["systemctl", "--user", "stop", *[s + ".service" for s in SERVICES]], check=False)
    print("DEMO_STOPPED; original project and services remain unchanged on port 8000")


def status():
    for port, path in [(8001, "/api/health"), (8011, "/health"), (8031, "/health"), (8081, "/health")]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=4) as response:
                print(port, response.status, response.read().decode())
        except Exception as exc:
            print(port, type(exc).__name__)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "preview", "activate", "rollback", "status"])
    action = parser.parse_args().action
    {"prepare": prepare, "preview": preview, "activate": activate, "rollback": rollback, "status": status}[action]()
