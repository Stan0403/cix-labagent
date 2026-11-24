"""Read-only existing-session sample plus a clearly marked report layout fixture."""

import argparse
import base64
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from labagent.models import Evidence, Measurement, ReadingCandidate, State  # noqa: E402
from labagent.plans import Plan  # noqa: E402
from labagent.report import html_report, markdown_report  # noqa: E402
from scripts.browser_board_smoke import CDP  # noqa: E402


def fixture(output):
    plan = Plan.model_validate_json(
        (ROOT / "configs/battery_plan.json").read_text(encoding="utf-8")
    ).model_dump()
    plan["measurement"]["samples"] = 2
    start = datetime(2026, 11, 24, 2, 0, tzinfo=timezone.utc)

    def at(seconds):
        return (start + timedelta(seconds=seconds)).isoformat()

    state = State(
        title="电池电压测量报告示例（软件演练）",
        mode="replay",
        plan=plan,
        status="completed",
        step=7,
        created_at=at(0),
        updated_at=at(320),
    )
    state.facts["objects"] = Evidence(value=plan["required_objects"], source="replay", at=at(20))
    state.facts["meter_range"] = Evidence(value="dc_20v", source="replay", at=at(30))
    for index, item in enumerate(plan["steps"]):
        if item["gate"] in {"read_sop", "meter_setup", "manual_check"}:
            state.checks[item["id"]] = Evidence(value=True, source="replay", at=at((index + 1) * 10))
    images = output / "fixture-images"
    images.mkdir(exist_ok=True)
    raw = (ROOT / "tests/fixtures/lcd/reading_1_353.png").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    (images / (digest + ".jpg")).write_bytes(raw)
    candidate = ReadingCandidate(
        value=1.35,
        reference="image:" + digest,
        at=at(180),
        meter_range="dc_20v",
        step_id="measure",
        status="confirmed",
        confirmed_value=1.353,
        raw_text="PRIVATE MODEL OUTPUT NOT FOR REPORT",
    )
    state.candidates.append(candidate)
    state.measurements.extend(
        [
            Measurement(value=1.2, source="replay", confidence=1, at=at(90), superseded=True),
            Measurement(
                value=1.353,
                source="replay",
                confidence=1,
                at=at(180),
                saved=True,
                reference=candidate.reference,
                candidate_id=candidate.id,
                original_value=1.35,
            ),
            Measurement(value=1.34, source="replay", confidence=1, at=at(240), saved=True),
        ]
    )
    events = []

    def action(seconds, kind, step, **data):
        events.append(
            {
                "sequence": len(events) + 1,
                "at": at(seconds),
                "kind": "user.action",
                "payload": {"input": {"kind": kind, **data}, "result": {"accepted": True}, "step": step},
            }
        )

    action(10, "confirm_step", 1, confirmed=True, advance_after=True)
    action(20, "confirm_equipment", 2, objects=plan["required_objects"], confirmed=True, advance_after=True)
    action(30, "confirm_step", 3, meter_range="dc_20v", confirmed=True, advance_after=True)
    action(60, "confirm_step", 4, confirmed=True, advance_after=True)
    action(100, "remeasure", 4)
    action(180, "confirm_candidate", 4, candidate_id=candidate.id, note="演练中对照显示屏图像修正小数位")
    action(250, "advance", 5)
    action(260, "save_measurement", 5, confirmed=True)
    action(270, "advance", 6)
    action(300, "confirm_step", 7, confirmed=True, advance_after=True)
    action(320, "report", 7)
    (output / "sample-report.html").write_text(
        html_report(state, plan, events, image_dir=images), encoding="utf-8"
    )
    (output / "sample-report.md").write_text(markdown_report(state, plan, events), encoding="utf-8")
    return {"fixture": True, "real_physical_measurement": False, "saved_samples": 2}


def existing(output, image_dir):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(path):
        with opener.open("http://127.0.0.1:8001/api" + path, timeout=12) as response:
            return json.load(response)

    sessions = get("/sessions")
    if not sessions:
        return {"available": False}

    def score(s):
        return (
            s.get("mode") == "live",
            any(m["saved"] and not m["superseded"] for m in s.get("measurements", [])),
            s.get("status") == "completed",
            s.get("updated_at", ""),
        )

    selected = max(sessions, key=score)
    sid = selected["id"]
    payload = get("/sessions/" + sid)
    state = State.model_validate({k: v for k, v in payload.items() if k in State.model_fields})
    events = []
    after = 0
    while page := get(f"/sessions/{sid}/events?after={after}"):
        events.extend(page)
        after = page[-1]["sequence"]
    spec = state.plan or json.loads((ROOT / "configs/experiment.json").read_text(encoding="utf-8"))
    (output / "existing-report.html").write_text(
        html_report(state, spec, events, image_dir=image_dir), encoding="utf-8"
    )
    (output / "existing-report.md").write_text(markdown_report(state, spec, events), encoding="utf-8")
    return {
        "available": True,
        "session_id": sid,
        "status": state.status,
        "mode": state.mode,
        "saved_samples": sum(m.saved and not m.superseded for m in state.measurements),
        "read_only": True,
        "revision": state.revision,
    }


def browser_check(output, browser=None):
    browser = browser or shutil.which("chromium")
    if not browser:
        raise RuntimeError("Provide --browser or install Chromium for report layout verification")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        debug = sock.getsockname()[1]
    with (
        tempfile.TemporaryDirectory(prefix="labagent-report-browser-") as directory,
        (output / "chromium.log").open("wb") as log,
    ):
        process = subprocess.Popen(
            [
                browser,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--no-first-run",
                "--disable-background-networking",
                "--disable-dev-shm-usage",
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={debug}",
                f"--user-data-dir={directory}",
                "--window-size=1180,1000",
                "about:blank",
            ],
            stdout=log,
            stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            info = None
            for _ in range(80):
                try:
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{debug}/json/new?{(output / 'sample-report.html').as_uri()}",
                        method="PUT",
                    )
                    with urllib.request.urlopen(request, timeout=1) as response:
                        info = json.load(response)
                    break
                except OSError:
                    time.sleep(0.2)
            assert info, "Chromium did not start"
            c = CDP(info["webSocketDebuggerUrl"])
            c.call("Runtime.enable")
            c.call("Network.enable")
            c.call("Page.bringToFront")
            c.wait(
                "document.querySelectorAll('h2').length===8&&[...document.images].every(i=>i.complete&&i.naturalWidth>0)"
            )
            assert c.evaluate("document.images.length>0")
            assert c.evaluate(
                "!document.body.innerText.includes('PRIVATE MODEL OUTPUT')&&!document.body.innerText.includes('SHA-256')&&!document.body.innerText.includes('附件')"
            )

            def screenshot(name):
                data = c.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
                (output / name).write_bytes(base64.b64decode(data["data"]))

            screenshot("report-overview.png")
            c.evaluate("document.querySelectorAll('h2')[4].scrollIntoView()")
            screenshot("report-data.png")
            c.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
            )
            c.evaluate("window.scrollTo(0,0)")
            assert c.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            screenshot("report-mobile.png")
            c.call("Emulation.setEmulatedMedia", {"media": "print"})
            assert c.evaluate("document.querySelectorAll('h2').length===8")
            assert not c.errors, c.errors
            return {
                "passed": True,
                "sections": 8,
                "embedded_images": c.evaluate("document.images.length"),
                "javascript_errors": c.errors,
                "mobile_overflow": False,
                "print_styles_checked": True,
            }
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image-dir", type=Path)
    parser.add_argument("--browser", help="Optional Chrome/Chromium executable path")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"sample": fixture(output)}
    if args.image_dir:
        report["existing_session"] = existing(output, args.image_dir)
    report["browser"] = browser_check(output, args.browser)
    (output / "report-validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
