"""Board-only staged validation and guarded installation of readout polarity checks."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

APP = Path("/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo")
WORK = Path.home() / ".local/state/codex-battery-polarity/20261124-completion"
CANDIDATE = WORK / "candidate"
PYTHON = APP / ".venv-app/bin/python"
WEB = "labagent-demo-web.service"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def write(name, value):
    (WORK / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def entries():
    result = json.loads((WORK / "manifest.json").read_text())
    for item in result:
        path = (APP / item["path"]).resolve()
        if not path.is_relative_to(APP) or Path(item["path"]).parts[0] not in {
            "labagent",
            "tests",
            "configs",
            "scripts",
            "docs",
        }:
            raise ValueError("Invalid source path")
    return result


def unchanged(items):
    changed = [
        item["path"] for item in items if digest(APP / item["path"]) != item["before"]
    ]
    if changed:
        raise RuntimeError(
            "Concurrent source edits require a fresh merge: " + ", ".join(changed)
        )


def prepare():
    items = entries()
    unchanged(items)
    CANDIDATE.mkdir(exist_ok=True)
    for name in (
        "labagent",
        "tests",
        "configs",
        "scripts",
        "knowledge",
        "docs",
        "fixtures",
    ):
        shutil.copytree(
            APP / name,
            CANDIDATE / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            dirs_exist_ok=True,
        )
    shutil.copy2(APP / "pyproject.toml", CANDIDATE / "pyproject.toml")
    allowed = {item["path"]: item for item in items}
    with tarfile.open(WORK / "patch.tgz", "r:gz") as archive:
        for item in archive.getmembers():
            if not item.isfile() or item.name not in allowed:
                raise ValueError("Unexpected patch member")
            data = archive.extractfile(item).read()
            if hashlib.sha256(data).hexdigest() != allowed[item.name]["after"]:
                raise ValueError("Patch checksum mismatch")
            target = CANDIDATE / item.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    assert all(digest(CANDIDATE / item["path"]) == item["after"] for item in items)
    with (WORK / "validation-launch.log").open("wb") as output:
        process = subprocess.Popen(
            [str(PYTHON), str(Path(__file__).resolve()), "validate"],
            stdout=output,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    write(
        "preparation.json",
        {"sources_match": True, "files": len(items), "validation_pid": process.pid},
    )
    print(
        json.dumps(
            {"prepared": True, "files": len(items), "validation_pid": process.pid}
        )
    )


def validate():
    with (WORK / "pytest.log").open("wb") as output:
        result = subprocess.run(
            [
                str(PYTHON),
                "-m",
                "pytest",
                "--disable-warnings",
                "--junitxml=" + str(WORK / "board-junit.xml"),
            ],
            cwd=CANDIDATE,
            check=False,
            env={**os.environ, "PYTHONPATH": str(CANDIDATE)},
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=300,
        )
    write(
        "validation.json",
        {"exit_code": result.returncode, "passed": result.returncode == 0},
    )


def snapshot(database):
    with sqlite3.connect(database) as connection:
        states = {
            sid: json.loads(state)
            for sid, state in connection.execute("SELECT id,state FROM sessions")
        }
        counts = {
            table: connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            for table in ("sessions", "events", "messages", "agent_runs")
        }
    return {"counts": counts, "states": states}


def service(action):
    subprocess.run(["systemctl", "--user", action, WEB], check=True, timeout=30)


def install():
    items = entries()
    validation = json.loads((WORK / "validation.json").read_text())
    browser = json.loads((WORK / "browser-validation.json").read_text())
    assert (
        validation["passed"] and browser["passed"] and not browser["javascript_errors"]
    )
    suites = ElementTree.parse(WORK / "board-junit.xml").getroot()
    assert not any(
        int(s.get("failures", "0")) or int(s.get("errors", "0"))
        for s in suites.iter("testsuite")
    )
    unchanged(items)
    sys.path.insert(0, str(APP))
    from labagent.config import Settings

    settings = Settings.load()
    environment = subprocess.check_output(
        ["systemctl", "--user", "show", WEB, "-p", "Environment", "--value"], text=True
    )
    env = dict(part.split("=", 1) for part in shlex.split(environment) if "=" in part)
    data = Path(env.get("LABAGENT_DATA_DIR", str(settings.data_dir)))
    if not data.is_absolute():
        data = APP / data
    database = data / "labagent.sqlite3"
    backup = WORK / "backup"
    backup.mkdir()
    service("stop")
    installed = []
    try:
        unchanged(items)
        before = snapshot(database)
        with (
            sqlite3.connect(database) as source,
            sqlite3.connect(backup / "labagent.sqlite3") as target,
        ):
            source.backup(target)
        write("data-before.json", before)
        for item in items:
            target = APP / item["path"]
            if digest(target) != item["before"]:
                raise RuntimeError(
                    "Source changed during installation: " + item["path"]
                )
            if target.exists():
                saved = backup / item["path"]
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".codex-polarity.tmp")
            shutil.copy2(CANDIDATE / item["path"], temporary)
            os.replace(temporary, target)
            installed.append(item)
        service("start")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        token = env.get("LABAGENT_ACCESS_TOKEN", settings.access_token)
        headers = {"Authorization": "Bearer " + token} if token else {}
        for _ in range(45):
            try:
                request = urllib.request.Request(
                    "http://127.0.0.1:8001/api/health", headers=headers
                )
                with opener.open(request, timeout=3) as response:
                    health = json.load(response)
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError("Web health did not recover")
        request = urllib.request.Request(
            "http://127.0.0.1:8001/api/plans/battery-voltage-15v", headers=headers
        )
        with opener.open(request, timeout=5) as response:
            plan = json.load(response)
        assert plan["measurement"]["polarity_check"] == "positive"
        assert not any(
            step.get("visual_check") in {"battery_contact", "battery_cleanup"}
            or step["id"] in {"contact", "cleanup"}
            for step in plan["steps"]
        )
        after = snapshot(database)
        assert set(before["states"]) == set(after["states"])
        for sid, original in before["states"].items():
            current = after["states"][sid]
            for key in ("plan", "measurements", "facts", "checks", "candidates"):
                assert original.get(key) == current.get(key), (sid, key)
        assert after["counts"]["messages"] == before["counts"]["messages"]
        assert after["counts"]["events"] >= before["counts"]["events"]
        assert after["counts"]["agent_runs"] == before["counts"]["agent_runs"]
        assert all(digest(APP / item["path"]) == item["after"] for item in items)
        write("data-after.json", after)
        report = {
            "deployed": True,
            "files": len(items),
            "health": health,
            "steps": len(plan["steps"]),
            "positive_policy": True,
            "before_counts": before["counts"],
            "after_counts": after["counts"],
            "historical_contents_preserved": True,
            "database_backup": str(backup / "labagent.sqlite3"),
            "running_before": sum(
                s["status"] == "running" for s in before["states"].values()
            ),
            "running_after": sum(
                s["status"] == "running" for s in after["states"].values()
            ),
            "tests": sum(int(s.get("tests", "0")) for s in suites.iter("testsuite")),
        }
        write("deployment.json", report)
        print(json.dumps(report, ensure_ascii=False))
    except Exception:
        service("stop")
        preserved = []
        for item in reversed(installed):
            target, saved = APP / item["path"], backup / item["path"]
            if digest(target) != item["after"]:
                preserved.append(item["path"])
            elif saved.exists():
                shutil.copy2(saved, target)
            elif item["before"] is None and digest(target) == item["after"]:
                target.unlink()
        # A previous model schema does not know the new UI reminder field.
        # Remove only that presentation timer on code rollback; keep all experiment data.
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE sessions SET state=json_remove(state,'$.completion_notice_until') WHERE json_type(state,'$.completion_notice_until') IS NOT NULL"
            )
        service("start")
        write(
            "deployment.json",
            {
                "deployed": False,
                "code_restored": not preserved,
                "concurrent_edits_preserved": preserved,
                "database_restored": False,
            },
        )
        raise


def audit():
    suffixes = {
        ".py",
        ".js",
        ".cjs",
        ".mjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".html",
        ".css",
        ".json",
        ".md",
        ".toml",
        ".sh",
        ".ps1",
        ".cmd",
        ".txt",
        ".yaml",
        ".yml",
        ".ini",
        ".cfg",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".service",
        ".svg",
        ".lock",
    }
    skip = {
        ".git",
        "__pycache__",
        "node_modules",
        "models",
        "data",
        ".runtime",
        "outputs",
        "work",
        "build",
        "dist",
        ".pytest_cache",
        ".cache",
        "test-results",
        ".tmp",
        ".ruff_cache",
    }
    inventory = []
    for directory, dirs, files in os.walk(APP):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in skip
            and not d.startswith(".venv")
            and not d.endswith(".egg-info")
        )
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink() or (
                name.startswith(".env") and name != ".env.example"
            ):
                continue
            if path.suffix not in suffixes and name not in {
                ".env.example",
                ".gitignore",
                "LICENSE",
                "Makefile",
                "Dockerfile",
            }:
                continue
            stat = path.stat()
            inventory.append(
                {
                    "project": "LabAgent-demo",
                    "path": "LabAgent-demo/" + path.relative_to(APP).as_posix(),
                    "remote": str(path),
                    "bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "sha256": digest(path),
                }
            )
    write("installed-source-inventory.json", inventory)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    health = {}
    for port in (8031, 8035, 8081, 8011, 8032, 8034):
        try:
            with opener.open(f"http://127.0.0.1:{port}/health", timeout=4) as response:
                health[str(port)] = {
                    "http": response.status,
                    "body": json.load(response),
                }
        except OSError as error:
            health[str(port)] = {"error": str(error)}
    services = {}
    for unit in (
        "labagent-demo-web",
        "labagent-demo-vlm",
        "labagent-demo-yolo",
        "labagent-demo-llm",
        "labagent-demo-asr",
        "labagent-demo-equipment-vlm",
        "labagent-demo-npu-vision",
    ):
        result = subprocess.run(
            ["systemctl", "--user", "is-active", unit + ".service"],
            capture_output=True,
            text=True,
            check=False,
        )
        services[unit] = result.stdout.strip()
    passed = all(v == "active" for v in services.values()) and all(
        v.get("http") == 200 for v in health.values()
    )
    passed = passed and all(
        digest(APP / item["path"]) == item["after"] for item in entries()
    )
    write(
        "audit.json",
        {
            "passed": passed,
            "sources": len(inventory),
            "services": services,
            "health": health,
        },
    )
    with tarfile.open("/tmp/codex-battery-polarity-final.tgz", "w:gz") as archive:
        for name in (
            "deployment.json",
            "validation.json",
            "board-junit.xml",
            "reader-sanity.json",
            "audit.json",
            "installed-source-inventory.json",
            "pytest.log",
            "preparation.json",
            "manifest.json",
            "browser-validation.json",
        ):
            if (WORK / name).is_file():
                archive.add(WORK / name, arcname=name)
    print(
        json.dumps({"passed": passed, "sources": len(inventory), "services": services})
    )


if __name__ == "__main__":
    {"prepare": prepare, "validate": validate, "install": install, "audit": audit}[
        sys.argv[1]
    ]()
