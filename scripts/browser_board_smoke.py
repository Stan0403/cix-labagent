"""Exercise the actual O6 browser, server and VLM in an explicitly marked replay session."""
import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "test-results"
BASE = "http://127.0.0.1:8001"


def request(path, body=None, method=None):
    raw = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=raw, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.load(response)


class CDP:
    def __init__(self, url):
        parsed = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=20)
        self.buffer = b""
        self.sequence = 0
        self.errors = []
        self.requests = []
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {parsed.path} HTTP/1.1\r\nHost: {parsed.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        data = b""
        while b"\r\n\r\n" not in data:
            data += self.sock.recv(4096)
        header, self.buffer = data.split(b"\r\n\r\n", 1)
        expected = base64.b64encode(hashlib.sha1((key+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        assert b"101" in header and expected in header

    def read(self, length):
        while len(self.buffer) < length:
            chunk = self.sock.recv(max(4096, length-len(self.buffer)))
            if not chunk:
                raise RuntimeError("Browser disconnected")
            self.buffer += chunk
        data, self.buffer = self.buffer[:length], self.buffer[length:]
        return data

    def receive(self):
        content = bytearray()
        while True:
            first, second = self.read(2)
            length = second & 127
            if length == 126:
                length = struct.unpack("!H", self.read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.read(8))[0]
            mask = self.read(4) if second & 128 else None
            body = self.read(length)
            if mask:
                body = bytes(x ^ mask[i % 4] for i, x in enumerate(body))
            if first & 15 == 8:
                raise RuntimeError("Browser closed WebSocket")
            if first & 15 in {0, 1}:
                content.extend(body)
                if first & 128:
                    return json.loads(content)

    def call(self, method, params=None):
        self.sequence += 1
        raw = json.dumps({"id": self.sequence, "method": method, "params": params or {}}).encode()
        mask = os.urandom(4)
        size = len(raw)
        header = bytes([129, 128 | size]) if size < 126 else (bytes([129, 254]) + struct.pack("!H", size)) if size < 65536 else bytes([129, 255]) + struct.pack("!Q", size)
        self.sock.sendall(header + mask + bytes(x ^ mask[i % 4] for i, x in enumerate(raw)))
        while True:
            result = self.receive()
            if result.get("method") == "Runtime.exceptionThrown":
                self.errors.append(result["params"])
            if result.get("method") == "Network.requestWillBeSent":
                self.requests.append(result["params"]["request"]["url"])
            if result.get("id") == self.sequence:
                if "error" in result:
                    raise RuntimeError(result["error"])
                return result.get("result", {})

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True})
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    def wait(self, expression, timeout=30):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.evaluate(expression):
                return
            time.sleep(.15)
        raise TimeoutError(expression)

    def click(self, selector):
        self.evaluate(f"document.querySelector({json.dumps(selector)}).click()")

    def check_human(self):
        self.evaluate("document.getElementById('human-confirm').checked=true")

    def screenshot(self, filename):
        metrics = self.call("Page.getLayoutMetrics")
        size = metrics["contentSize"]
        result = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
            "clip": {"x": 0, "y": 0, "width": size["width"], "height": size["height"], "scale": 1}})
        (OUT / filename).write_bytes(base64.b64decode(result["data"]))


def run():
    OUT.mkdir(exist_ok=True)
    state = request("/api/sessions", {"title": "浏览器端到端演练", "mode": "replay", "plan_id": "battery-voltage-15v"})
    session_id = state["id"]
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="labagent-browser-") as profile, (OUT / "browser-chromium.log").open("wb") as log:
        process = subprocess.Popen([shutil.which("chromium"), "--headless", "--disable-gpu", "--no-sandbox", "--no-first-run",
            "--disable-background-networking", "--disable-dev-shm-usage", "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "--window-size=1440,1000", "about:blank"],
            stdout=log, stderr=subprocess.STDOUT)
        try:
            info = None
            for _ in range(80):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/new?{BASE}", timeout=1) as _unused:
                        pass
                except Exception:
                    try:
                        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{BASE}", method="PUT")
                        with urllib.request.urlopen(req, timeout=1) as response:
                            info = json.load(response)
                        break
                    except Exception:
                        time.sleep(.25)
            if not info:
                raise RuntimeError("Headless Chromium did not start")
            c = CDP(info["webSocketDebuggerUrl"])
            c.call("Runtime.enable")
            c.call("Network.enable")
            c.wait("document.getElementById('connection')?.textContent.includes('在线')")
            c.evaluate(f"sessionStorage.setItem('labagent_demo_session',{json.dumps(session_id)})")
            c.call("Page.reload")
            c.wait("document.getElementById('mode')?.textContent.includes('演练')")
            c.click("#hide-setup")
            c.click("#advance")
            time.sleep(.4)
            assert request(f"/api/sessions/{session_id}")["step"] == 0
            print("BROWSER blocked_unconfirmed_sop", flush=True)

            def confirm_and_advance(expected):
                c.check_human()
                c.click("#confirm-current")
                c.wait("document.querySelectorAll('#blockers li').length===0")
                c.click("#advance")
                c.wait(f"document.getElementById('step-count').textContent.startsWith('{expected} /')")

            confirm_and_advance(2)
            c.evaluate("document.querySelector('[value=battery].equipment-checkbox').checked=true;document.querySelector('[value=probes].equipment-checkbox').checked=true")
            c.check_human(); c.click("#confirm-equipment")
            time.sleep(.4)
            c.click("#advance"); time.sleep(.4)
            assert request(f"/api/sessions/{session_id}")["step"] == 1
            print("BROWSER missing_meter_blocked", flush=True)
            c.evaluate("document.querySelector('[value=multimeter].equipment-checkbox').checked=true")
            c.check_human(); c.click("#confirm-equipment")
            c.wait("document.querySelectorAll('#blockers li').length===0")
            c.click("#advance")
            c.wait("document.getElementById('step-count').textContent.startsWith('3 /')")
            c.evaluate("document.getElementById('meter-range').value='dc_20v'")
            confirm_and_advance(4)
            confirm_and_advance(5)
            document = c.call("DOM.getDocument")
            node = c.call("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": "#media-file"})
            image = ROOT.parent / "LabAgent/.tmp/board-runtime/vlm-test-meter.png"
            c.call("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(image)]})
            c.wait("!document.getElementById('preview').classList.contains('hidden')")
            rect = c.evaluate("(()=>{const r=document.getElementById('roi-overlay').getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})()")
            c.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": rect["x"]+1, "y": rect["y"]+1, "button": "left", "clickCount": 1})
            c.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": rect["x"]+rect["w"]-1, "y": rect["y"]+rect["h"]-1, "button": "left", "buttons": 1})
            c.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": rect["x"]+rect["w"]-1, "y": rect["y"]+rect["h"]-1, "button": "left", "clickCount": 1})
            c.click("#ocr")
            c.wait("!document.getElementById('candidate-panel').classList.contains('hidden')", timeout=120)
            value = c.evaluate("document.getElementById('candidate-value').value")
            assert value, "Real VLM did not produce a parseable candidate"
            assert not request(f"/api/sessions/{session_id}")["measurements"]
            c.screenshot("browser-reading-candidate.png")
            c.check_human(); c.click("#confirm-candidate")
            c.wait("document.querySelectorAll('#blockers li').length===0")
            c.click("#advance")
            c.wait("document.getElementById('step-count').textContent.startsWith('6 /')")
            c.check_human(); c.click("#save-measurement")
            c.wait("document.querySelectorAll('#blockers li').length===0")
            c.click("#advance")
            c.wait("document.getElementById('step-count').textContent.startsWith('7 /')")
            confirm_and_advance(8)
            c.click("#advance")
            c.wait("document.getElementById('run-status').textContent==='实验完成'")
            completed = request(f"/api/sessions/{session_id}")
            assert completed["measurements"][0]["source"] == "replay" and completed["measurements"][0]["saved"]
            print("BROWSER completed_replay_with_real_vlm", value, flush=True)
            c.screenshot("browser-completed.png")
            c.click("#show-setup")
            text = "文档驱动观察演示\n实验器材：记录纸、笔\n实验步骤\n1. 核对记录纸和笔。\n2. 观察现场物体并写下颜色。\n3. 整理记录，确认完成。"
            c.evaluate("document.getElementById('requirements').value=" + json.dumps(text) + ";document.getElementById('compile-method').value='rules'")
            c.click("#import-plan")
            c.wait("document.getElementById('plan-summary').textContent.includes('文档驱动观察演示')")
            assert c.evaluate("document.querySelectorAll('#plan-steps li').length") != 8
            assert not c.evaluate("document.getElementById('approve-plan').checked")
            c.screenshot("browser-document-plan.png")
            c.call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
            time.sleep(.5)
            assert c.evaluate("document.documentElement.scrollWidth <= window.innerWidth+1"), "Mobile horizontal overflow"
            c.screenshot("browser-mobile.png")
            external = [u for u in c.requests if u.startswith(("http://", "https://")) and urllib.parse.urlparse(u).hostname not in {"127.0.0.1", "localhost", "::1"}]
            result = {"session": session_id, "mode": "replay", "real_vlm_candidate": value, "completed": completed["status"],
                      "document_plan_variable_length": True, "mobile_overflow": False, "javascript_errors": c.errors, "external_page_requests": external}
            (OUT / "browser-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
            assert not c.errors and not external
            print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    run()
