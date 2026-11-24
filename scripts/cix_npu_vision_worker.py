"""Fixed-grid Qwen3-VL encoder: CIX NPU worker on a user-private Unix socket."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import time

import numpy as np
from NOE_Engine import EngineInfer

ROOT = Path(__file__).resolve().parent.parent
MAGIC = 0x51334E50
STOPPING = False


def receive(connection, size):
    chunks = []
    while size:
        data = connection.recv(min(size, 1 << 20))
        if not data:
            raise ConnectionError("Truncated encoder request")
        chunks.append(data)
        size -= len(data)
    return b"".join(chunks)


def stop(*_):
    global STOPPING
    STOPPING = True


def notify_ready():
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.connect(address)
            client.sendall(b"READY=1")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, default=ROOT / "models/qwen3-vl-4b-npu/vision.cix")
    args = parser.parse_args()
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    path = runtime / "labagent-npu-vision.sock"
    status = runtime / "labagent-npu-vision.json"
    if not runtime.is_dir() or len(os.fsencode(path)) >= 108:
        raise RuntimeError("A user runtime directory is required")
    if path.exists():
        if not stat.S_ISSOCK(path.stat().st_mode):
            raise RuntimeError("Encoder socket path is occupied")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            try:
                probe.connect(str(path))
            except ConnectionRefusedError:
                path.unlink()
            else:
                raise RuntimeError("Encoder worker is already running")
    engine = EngineInfer(str(args.model))
    state = {"ready": False, "pid": os.getpid(), "socket": str(path), "requests": 0, "errors": 0,
             "backend": "CIX NPU Qwen3-VL-4B vision", "model": str(args.model)}

    def save():
        temporary = status.with_suffix(".tmp")
        temporary.write_text(json.dumps(state))
        os.chmod(temporary, 0o600)
        temporary.replace(status)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        handles = []
        for fd in Path("/proc/self/fd").iterdir():
            try:
                target = os.readlink(fd)
                if target.startswith("/dev/aipu"):
                    handles.append(target)
            except OSError:
                pass
        if not handles:
            raise RuntimeError("Encoder did not open the NPU device")
        state["npu_device_handles"] = handles
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(path))
            os.chmod(path, 0o600)
            server.listen(2)
            server.settimeout(1)
            state["ready"] = True
            save()
            notify_ready()
            while not STOPPING:
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection:
                    connection.settimeout(30)
                    try:
                        header = struct.unpack("<4I", receive(connection, 16))
                        if header != (MAGIC, 320, 320, 320 * 320 * 3):
                            raise ValueError("Unsupported encoder input")
                        pixels = np.frombuffer(receive(connection, header[3] * 4), dtype="<f4").reshape(320, 320, 3)
                        if not np.isfinite(pixels).all():
                            raise ValueError("Nonfinite encoder input")
                        chw = pixels.transpose(2, 0, 1)
                        patches = np.repeat(chw[None], 2, axis=0).reshape(1, 2, 3, 10, 2, 16, 10, 2, 16)
                        patches = patches.transpose(0, 3, 6, 4, 7, 2, 1, 5, 8).reshape(400, 1536).copy()
                        started = time.monotonic()
                        values = engine.forward([patches])
                        if len(values) != 4:
                            raise ValueError("Unexpected encoder output count")
                        encoded = np.concatenate([np.asarray(v).reshape(100, 2560) for v in values], axis=1).astype("<f4")
                        if not np.isfinite(encoded).all():
                            raise ValueError("Nonfinite encoder output")
                        state["last_duration_ms"] = round((time.monotonic() - started) * 1000, 3)
                        connection.sendall(struct.pack("<2I", MAGIC, encoded.size) + encoded.tobytes())
                        state["requests"] += 1
                    except Exception as error:
                        state["errors"] += 1
                        print("Encoder request rejected: " + repr(error), flush=True)
                        try:
                            connection.sendall(struct.pack("<2I", MAGIC, 0))
                        except OSError:
                            pass
                    save()
    finally:
        state["ready"] = False
        save()
        engine.clean()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
