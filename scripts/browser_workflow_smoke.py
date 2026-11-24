"""Isolated replay-only browser validation. Fixture responses are NOT real VLM results."""
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import uvicorn
from labagent.config import Settings
from labagent.main import create_app
from scripts import browser_board_smoke as browser

OUT = ROOT / "test-results"


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def run():
    OUT.mkdir(exist_ok=True)
    calls, counts = [], {}
    port, debug_port = free_port(), free_port()
    with tempfile.TemporaryDirectory(prefix="labagent-workflow-qa-") as directory:
        settings = Settings(data_dir=Path(directory)/"data", evidence_ttl_seconds=900)
        app = create_app(settings)
        def fixture_request(base, key, path, **kwargs):
            calls.append(path)
            counts[path] = counts.get(path, 0) + 1
            if path == "inspect":
                labels = json.loads(kwargs["data"]["labels"])
                return {"present": {key: True for key in labels}, "boxes": [], "raw_text": "BROWSER FIXTURE, NOT A MODEL RESULT"}
            if path == "check_step":
                rule = kwargs["data"]["rule"]
                counts[rule] = counts.get(rule, 0) + 1
                keys = {"meter_setup": ["black_in_com", "red_in_voltage", "dc_voltage_mode"],
                        "battery_contact": ["red_on_positive", "black_on_negative", "no_short"],
                        "battery_cleanup": ["probes_removed", "meter_off"]}[rule]
                checks = {key: True for key in keys}
                if counts[rule] == 1:
                    checks[keys[-1]] = None
                return {"checks": checks, "meter_range": "dc_20v", "raw_text": "BROWSER FIXTURE, NOT A MODEL RESULT"}
            if path == "locate_display":
                return {"roi": [.1, .1, .8, .8]}
            if path == "recognize":
                return {"text": '{"reading":"1.34","unit":null}', "backend": "browser-fixture-not-model"}
            raise AssertionError("Unexpected tool request: " + path)
        app.state.agent.gateway.request = fixture_request
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True); thread.start()
        base = f"http://127.0.0.1:{port}"
        def request(path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base+path, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.load(response)
        for _ in range(80):
            try:
                request("/api/health"); break
            except OSError:
                time.sleep(.1)
        state = request("/api/sessions", {"mode": "replay", "plan_id": "battery-voltage-15v"})
        session_id = state["id"]
        with (OUT/"workflow-browser.log").open("wb") as log:
            process = subprocess.Popen([shutil.which("chromium"), "--headless", "--disable-gpu", "--no-sandbox", "--no-first-run",
                "--disable-background-networking", "--disable-dev-shm-usage", "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream", "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={debug_port}", f"--user-data-dir={directory}/chrome", "--window-size=1440,1000", "about:blank"],
                stdout=log, stderr=subprocess.STDOUT)
            try:
                info = None
                for _ in range(80):
                    try:
                        req = urllib.request.Request(f"http://127.0.0.1:{debug_port}/json/new?{base}", method="PUT")
                        with urllib.request.urlopen(req, timeout=1) as response:
                            info = json.load(response)
                        break
                    except OSError:
                        time.sleep(.2)
                assert info, "Chromium failed to start"
                c = browser.CDP(info["webSocketDebuggerUrl"])
                c.call("Runtime.enable"); c.call("Network.enable")
                c.wait("document.getElementById('connection')?.textContent.includes('在线')")
                c.evaluate(f"sessionStorage.setItem('labagent_demo_session','{session_id}')")
                c.call("Page.reload")
                c.wait("document.getElementById('mode')?.textContent.includes('演练')")
                c.click("#hide-setup")
                c.check_human(); c.click("#confirm-current")
                c.wait("document.querySelectorAll('#blockers li').length===0")
                c.click("#advance")
                c.wait("document.getElementById('step-count').textContent.startsWith('2 /')")
                c.click("#observe-loop")
                c.wait("document.querySelectorAll('.equipment-checkbox:checked').length===3", timeout=30)
                assert request(f"/api/sessions/{session_id}")["step"] == 1, "Equipment requires final confirmation"
                c.check_human(); c.click("#confirm-equipment")
                c.wait("document.getElementById('step-count').textContent.startsWith('3 /')")
                c.wait("document.querySelectorAll('#workflow-checks .check-unknown').length>0")
                assert request(f"/api/sessions/{session_id}")["step"] == 2, "Unknown check must block advancement"
                c.screenshot("workflow-meter-check.png")
                # No further human confirmations: camera fixtures exercise the entire workflow.
                c.wait("document.getElementById('run-status').textContent==='实验完成'", timeout=120)
                state = request(f"/api/sessions/{session_id}")
                assert state["mode"] == "replay" and state["status"] == "completed"
                assert len(state["measurements"]) == 1 and state["measurements"][0]["saved"]
                assert state["measurements"][0]["source"] == "replay"
                assert len(state["measurements"][0]["supporting_candidate_ids"]) == 2
                assert all(counts.get(rule, 0) >= 2 for rule in ("meter_setup", "battery_contact", "battery_cleanup"))
                c.screenshot("workflow-completed.png")
                c.call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
                assert c.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
                external = [url for url in c.requests if url.startswith(("http://", "https://")) and not url.startswith(base+"/")]
                result = {"validation": "isolated replay with fixture responses; not physical or model accuracy validation",
                          "completed": True, "automatic_after_equipment_confirmation": True, "tool_counts": counts,
                          "javascript_errors": c.errors, "external_browser_requests": external, "mobile_overflow": False}
                (OUT/"workflow-browser-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
                assert not c.errors and not external
                print(json.dumps(result, ensure_ascii=False), flush=True)
            except Exception:
                try:
                    diagnostic = {"tool_counts": counts, "state": request(f"/api/sessions/{session_id}"),
                                  "browser_errors": c.errors, "browser_status": c.evaluate("document.getElementById('observation-status').textContent")}
                    (OUT/"workflow-browser-failure.json").write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2))
                    c.screenshot("workflow-browser-failure.png")
                except Exception:
                    pass
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
                server.should_exit = True
                thread.join(timeout=10)


if __name__ == "__main__":
    run()
