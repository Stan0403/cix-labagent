"""Deploy a reviewed candidate on the board, refusing concurrent teammate edits."""

import argparse
import hashlib
import json
import py_compile
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path

SERVICES = ("labagent-demo-vlm.service", "labagent-demo-web.service")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def contained(root, relative):
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Invalid manifest path")
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Manifest path escapes application root")
    return target


def restart():
    subprocess.run(["systemctl", "--user", "restart", *SERVICES], check=True, timeout=45)


def healthy():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(40):
        try:
            with opener.open("http://127.0.0.1:8001/api/health", timeout=2) as response:
                data = json.load(response)
            with opener.open("http://127.0.0.1:8031/openapi.json", timeout=2) as response:
                paths = json.load(response)["paths"]
            if data.get("capabilities", {}).get("agent_controller") == "llm" and "/inspect_semantic" in paths:
                return True
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.25)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--model-report", type=Path)
    args = parser.parse_args()
    app, candidate = args.app.resolve(), args.candidate.resolve()
    if app == candidate or not (app / "labagent/main.py").is_file():
        raise SystemExit("Use a separate candidate directory and the actual application root")
    manifest = json.loads((candidate / "agent-update-manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        source = contained(candidate, entry["path"])
        target = contained(app, entry["path"])
        if digest(source) != entry["sha256"]:
            raise SystemExit("Candidate hash mismatch: " + entry["path"])
        if digest(target) not in {entry["baseline_sha256"], entry["sha256"]}:
            raise SystemExit("Teammate edit detected; merge before deployment: " + entry["path"])
        if source.suffix == ".py":
            py_compile.compile(str(source), doraise=True)
    print("PREFLIGHT_OK", len(manifest["files"]), flush=True)
    if not args.activate:
        return
    if not args.model_report:
        raise SystemExit("Activation requires the real local LLM preflight report (--model-report)")
    report = json.loads(args.model_report.read_text(encoding="utf-8"))
    if not report.get("real_llm") or not report.get("contract_passed") or len(report.get("cases", [])) < 3:
        raise SystemExit("Model preflight has not passed; production files were not changed")
    backup = app / ".runtime" / ("agent-update-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(parents=True)
    absent = []
    for entry in manifest["files"]:
        path = entry["path"]
        target = contained(app, path)
        saved = contained(backup, path)
        saved.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.copy2(target, saved)
        else:
            absent.append(path)
    (backup / "new-files.json").write_text(json.dumps(absent), encoding="utf-8")
    try:
        for entry in manifest["files"]:
            source = contained(candidate, entry["path"])
            target = contained(app, entry["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".agent-update")
            shutil.copy2(source, temporary)
            temporary.replace(target)
        restart()
        if not healthy():
            raise RuntimeError("New API or semantic route did not become healthy")
        print("ACTIVATED", app, "BACKUP", backup, flush=True)
    except Exception:
        for entry in manifest["files"]:
            path = entry["path"]
            target = contained(app, path)
            if path in absent:
                target.unlink(missing_ok=True)
            else:
                shutil.copy2(contained(backup, path), target)
        restart()
        print("ROLLED_BACK", backup, flush=True)
        raise


if __name__ == "__main__":
    main()
