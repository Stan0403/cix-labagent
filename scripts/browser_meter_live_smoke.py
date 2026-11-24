"""First-entry detection and click-only retries, with fixtures and isolated experiment data."""

import argparse
import base64
import io
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
from fastapi import HTTPException
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from labagent.config import Settings  # noqa: E402
from labagent.main import create_app  # noqa: E402
from labagent.models import Evidence, NewSession  # noqa: E402
from scripts import browser_board_smoke as browser  # noqa: E402


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


CAMERA = """window.testCanvas=document.createElement('canvas');testCanvas.width=640;testCanvas.height=480;
window.drawMeter=(color)=>{window.fixtureColor=color;const c=testCanvas.getContext('2d');c.fillStyle='#aaa';c.fillRect(0,0,640,480);
c.fillStyle='#222';c.fillRect(100,35,440,420);c.fillStyle=color;c.fillRect(260,140,120,120);
c.fillStyle='white';c.font='25px sans-serif';c.fillText('EXPLICIT CAMERA FIXTURE',125,100);};
drawMeter('red');window.fixtureFrameTimer=setInterval(()=>drawMeter(window.fixtureColor),80);
navigator.mediaDevices.getUserMedia=async()=>testCanvas.captureStream(10);"""


def run(output, executable=None):
    output.mkdir(parents=True, exist_ok=True)
    browser.OUT = output
    report = {"fixture_models": True, "isolated_data": True, "cases": []}
    controls = {"hold": False}
    calls = []
    active = 0
    maximum = 0
    guard = threading.Lock()
    with tempfile.TemporaryDirectory(prefix="labagent-meter-qa-") as directory:
        settings = Settings(
            data_dir=Path(directory) / "data",
            meter_live_enabled=True,
            llm_base_url="http://127.0.0.1:1/v1",
            llm_model="explicit-fixture",
        )
        app = create_app(settings)
        plan = app.state.plans.get("battery-voltage-15v").model_dump()

        def create():
            state = app.state.store.create(NewSession(automation="agent"), json.loads(json.dumps(plan)))

            def prepare(s):
                s.step = 2
                s.checks[plan["steps"][0]["id"]] = Evidence(value=True, source="manual")
                s.facts["objects"] = Evidence(value=plan["required_objects"], source="manual")
                return {"accepted": True}

            return app.state.store.mutate(state.id, "test.fixture", {}, prepare)[0]

        def fixture(base, key, route, **kwargs):
            nonlocal active, maximum
            if route == "chat/completions":
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "tool": "ask_user",
                                        "arguments": {"message": "EXPLICIT NEXT STEP HOLD"},
                                        "reason": "fixture",
                                    }
                                )
                            }
                        }
                    ]
                }
            assert route == "check_meter_live", route
            with guard:
                active += 1
                maximum = max(maximum, active)
            try:
                with Image.open(io.BytesIO(kwargs["files"]["file"][1])) as image:
                    pixel = image.getpixel((image.width // 2, image.height // 2))
                calls.append(pixel)
                if controls.get("error"):
                    raise HTTPException(controls["error"], "EXPLICIT MODEL ERROR FIXTURE")
                deadline = time.monotonic() + 5
                while controls["hold"] and time.monotonic() < deadline:
                    time.sleep(0.05)
                time.sleep(1.2 if len(calls) == 1 else 0.4)
                red = pixel[0] > pixel[1] + 60 and pixel[0] > pixel[2] + 60
                detail = io.BytesIO()
                Image.new("RGB", (320, 320), pixel).save(detail, format="JPEG")
                detail_url = "data:image/jpeg;base64," + base64.b64encode(detail.getvalue()).decode()
                return {
                    "checks": {"black_in_com": True, "red_in_voltage": not red, "dc_voltage_mode": True},
                    "meter_range": "dc_20v",
                    "roi": [0.12, 0.03, 0.76, 0.94],
                    "backend": "explicit-fixture",
                    "detail_mode": "separate-dial-terminals-v1",
                    "detail_views": [
                        {"part": part, "roi": roi, "image_url": detail_url}
                        for part, roi in [
                            ("dial", [0.12, 0.25, 0.76, 0.45]),
                            ("terminals", [0.12, 0.70, 0.76, 0.27]),
                        ]
                    ],
                }
            finally:
                with guard:
                    active -= 1

        app.state.agent.gateway.request = fixture
        server_port, debug = port(), port()
        base = f"http://127.0.0.1:{server_port}"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=server_port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        def get(path):
            with urllib.request.urlopen(base + "/api" + path, timeout=10) as response:
                return json.load(response)

        for _ in range(80):
            try:
                get("/health")
                break
            except OSError:
                time.sleep(0.1)
        with (output / "chromium.log").open("wb") as log:
            process = subprocess.Popen(
                [
                    executable or shutil.which("chromium"),
                    "--headless",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--no-first-run",
                    "--disable-background-networking",
                    "--disable-extensions",
                    "--disable-default-apps",
                    "--use-fake-ui-for-media-stream",
                    "--remote-debugging-address=127.0.0.1",
                    f"--remote-debugging-port={debug}",
                    f"--user-data-dir={directory}/chrome",
                    "--window-size=1440,1100",
                    "about:blank",
                ],
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            c = None
            try:
                for _ in range(80):
                    try:
                        request = urllib.request.Request(
                            f"http://127.0.0.1:{debug}/json/new?{base}", method="PUT"
                        )
                        with urllib.request.urlopen(request, timeout=1) as response:
                            info = json.load(response)
                        break
                    except OSError:
                        time.sleep(0.2)
                c = browser.CDP(info["webSocketDebuggerUrl"])
                c.call("Runtime.enable")
                c.call("Network.enable")
                c.wait("typeof S!=='undefined'&&S.config?.providers.meter_live_enabled&&S.plan")
                c.evaluate(CAMERA)
                c.evaluate(
                    "window.originalMeterCapture=capture;window.meterCaptureCount=0;capture=async(...args)=>{window.meterCaptureCount++;return originalMeterCapture(...args);};"
                )
                state = create()
                sid = state.id
                c.evaluate(f"selectSession({json.dumps(sid)},{{fresh:true}})")
                c.wait("S.meterBusy&&S.meterStartedAt&&!!S.media")
                # Wait for the first actual fixture inference before changing it.
                deadline = time.monotonic() + 8
                while not calls and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert calls
                c.evaluate("drawMeter('blue')")
                c.wait(
                    "!S.meterBusy&&document.getElementById('primary-action').dataset.action==='inspect_meter'",
                    timeout=15,
                )
                time.sleep(1.2)
                assert len(calls) == 1 and c.evaluate("meterCaptureCount===1"), (
                    "Only one photo is captured per round"
                )
                assert c.evaluate("S.meterResult?.checks.red_in_voltage===false"), (
                    "Result must belong to captured red photo, not later blue video"
                )
                c.wait(
                    "document.getElementById('meter-snapshot-image').complete&&document.getElementById('meter-snapshot-image').naturalWidth>0"
                )
                c.evaluate("document.getElementById('meter-snapshot-panel').open=true")
                c.screenshot("snapshot-retained-during-video-change.png")
                report["cases"].append(
                    {"case": "one_snapshot_result_is_retained_despite_later_video_changes", "passed": True}
                )
                c.evaluate("drawMeter('red')")
                time.sleep(0.25)
                c.click("#primary-action")
                c.wait("!S.meterBusy&&S.meterResult?.checks.red_in_voltage===false", timeout=15)
                time.sleep(1.2)
                assert get("/sessions/" + sid)["step"] == 2
                assert "meter" not in get("/sessions/" + sid)["checks"]
                assert len(calls) == 2 and calls[0][0] > calls[0][1] and calls[-1][0] > calls[-1][1]
                c.wait("document.querySelectorAll('#meter-detail-views img').length===2")
                c.evaluate("document.getElementById('meter-detail-panel').open=true")
                c.wait(
                    "[...document.querySelectorAll('#meter-detail-views img')].every(i=>i.complete&&i.naturalWidth===320)"
                )
                c.screenshot("changed-frame-rechecked.png")
                report["cases"].append(
                    {
                        "case": "failed_check_keeps_result_and_waits_without_retrying",
                        "passed": True,
                    }
                )
                c.evaluate("drawMeter('blue')")
                time.sleep(1.2)
                assert len(calls) == 2, "Repositioning alone must not cause another inference"
                controls["hold"] = True
                c.evaluate(
                    "document.getElementById('primary-action').click();document.getElementById('primary-action').click();"
                )
                deadline = time.monotonic() + 8
                while len(calls) < 3 and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert len(calls) == 3
                c.evaluate("drawMeter('red')")
                controls["hold"] = False
                c.wait("S.session.step===3", timeout=15)
                assert len(calls) == 3, "Double clicking must not create duplicate checks"
                assert c.evaluate("meterCaptureCount===3"), (
                    "Passing must not take a second confirmation photo"
                )
                fact = get("/sessions/" + sid)["facts"]["workflow_check:meter"]
                assert fact["value"]["method"] == "single_frame_snapshot"
                assert "scene_verified_at" not in fact["value"]
                assert fact["source"] == "vision" and maximum == 1
                assert c.evaluate(
                    "document.getElementById('meter-detail-panel').classList.contains('hidden')"
                )
                report["cases"].append(
                    {
                        "case": "one_click_one_photo_and_snapshot_evidence_advances_without_second_capture",
                        "passed": True,
                    }
                )

                controls["hold"] = True
                state = create()
                sid = state.id
                c.evaluate(f"selectSession({json.dumps(sid)},{{fresh:true}})")
                c.wait("S.meterBusy&&!!S.media")
                c.click("#pause")
                c.wait("S.session.status==='paused'&&!S.meterActive")
                c.evaluate("window.meterOldDocument=true")
                c.call("Page.reload")
                c.wait("typeof S!=='undefined'&&!window.meterOldDocument&&S.session?.status==='paused'")
                assert get("/sessions/" + sid)["step"] == 2
                c.evaluate(CAMERA + "drawMeter('blue')")
                controls["hold"] = False
                c.click("#primary-action")
                c.wait(
                    "S.session.status==='running'&&!S.meterBusy&&document.getElementById('primary-action').dataset.action==='inspect_meter'",
                    timeout=15,
                )
                count_after_resume = len(calls)
                time.sleep(1.2)
                assert len(calls) == count_after_resume, (
                    "Resume/refresh must not erase the completed initial attempt"
                )
                c.click("#primary-action")
                c.wait("S.session.step===3", timeout=20)
                report["cases"].append(
                    {"case": "pause_refresh_resume_preserves_click_only_retry", "passed": True}
                )

                controls["hold"] = True
                state = create()
                sid = state.id
                c.evaluate(f"selectSession({json.dumps(sid)},{{fresh:true}})")
                c.wait("S.meterBusy&&!!S.media")
                c.evaluate("window.confirm=()=>true")
                c.wait(f"!document.querySelector('[data-delete-session=\"{sid}\"]').disabled")
                c.click('[data-delete-session="' + sid + '"]')
                c.wait("S.session===null&&!S.meterActive")
                controls["hold"] = False
                time.sleep(0.8)
                assert all(s["id"] != sid for s in get("/sessions"))
                report["cases"].append(
                    {
                        "case": "delete_during_inference_stops_live_loop_and_cannot_recreate_session",
                        "passed": True,
                    }
                )
                for status in (503, 429):
                    controls["error"] = status
                    state = create()
                    sid = state.id
                    count_before = len(calls)
                    c.evaluate(f"selectSession({json.dumps(sid)},{{fresh:true}})")
                    c.wait(
                        "meterWasTried()&&!S.meterBusy&&document.getElementById('primary-action').dataset.action==='inspect_meter'"
                    )
                    time.sleep(2)
                    assert len(calls) == count_before + 1, "Service errors/busy must not auto-retry"
                    assert get("/sessions/" + sid)["step"] == 2
                    report["cases"].append(
                        {"case": f"http_{status}_waits_for_explicit_retry", "passed": True}
                    )
                    controls["error"] = 0
                c.evaluate(f"selectSession({json.dumps(get('/sessions')[0]['id'])})")
                c.wait("S.session&&!S.switching")
                c.call(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
                )
                assert c.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
                c.screenshot("meter-mobile.png")
                assert not c.errors, c.errors
                report.update(
                    passed=True,
                    javascript_errors=c.errors,
                    maximum_concurrent_checks=maximum,
                    inference_frames=len(calls),
                    formal_records_modified=False,
                )
            except Exception:
                if c:
                    c.screenshot("failure.png")
                    report["diagnostic"] = c.evaluate(
                        "({id:S.session?.id,step:S.session?.step,status:S.session?.status,auto:S.autoEnabled,media:!!S.media,busy:S.meterBusy,active:S.meterActive,generation:S.meterGeneration,quiet:S.meterQuietAt,message:S.meterMessage,error:S.runError,result:S.meterResult,toast:document.getElementById('toast').textContent})"
                    )
                    report["javascript_errors"] = c.errors
                raise
            finally:
                (output / "meter-browser-validation.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                server.should_exit = True
                thread.join(timeout=10)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser")
    args = parser.parse_args()
    run(args.output, args.browser)
