"""Read-only readiness inventory for the isolated Qwen3 NPU adapter."""

import argparse
import glob
import hashlib
import json
import os
import platform
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path("/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo")
parser = argparse.ArgumentParser()
parser.add_argument("--output", default="/tmp/codex-qwen3-npu-preflight.json")
args = parser.parse_args()


def run(args):
    try:
        r = subprocess.run(
            args, text=True, capture_output=True, timeout=12, check=False
        )
        return {"exit": r.returncode, "out": r.stdout[-7000:], "err": r.stderr[-1000:]}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": str(e)}


def describe(path):
    p = Path(path)
    return (
        {"path": str(p), "bytes": p.stat().st_size, "resolved": str(p.resolve())}
        if p.exists()
        else {"path": str(p), "exists": False}
    )


headers = []
for name in (
    [
        "/usr/include/misc/armchina_aipu.h",
        "/usr/include/linux/armchina_aipu.h",
        "/usr/share/cix/include/npu/kmd/armchina_aipu.h",
    ]
    + glob.glob("/usr/src/*/include/armchina_aipu.h")
    + glob.glob(
        "/usr/src/cix-drivers-*/npu/driver/armchina-npu/include/armchina_aipu.h"
    )
):
    p = Path(name)
    if p.is_file():
        raw = p.read_bytes()
        headers.append(
            {
                **describe(p),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "extensions": [
                    line
                    for line in raw.decode(errors="replace").splitlines()
                    if any(
                        x in line
                        for x in ["WBUF", "SLOT", "CACHE_INVALID", "QUERY_CAP"]
                    )
                ][:30],
            }
        )
libraries = []
for name in [
    "libaiputoolchain.so",
    "libaiputoolchain_core.so",
    "libaipu_buildingtool.so",
    "libaipu_toolchain_core.so",
    "libaipu_buildtool.so",
    "libaipu_driver.so",
]:
    libraries.append(describe("/usr/share/cix/lib/onnxruntime/" + name))
models = [
    describe(p)
    for p in (ROOT.parent / "LabAgent/models/qwen3-4b").glob("*")
    if p.suffix in {".gguf", ".json"}
]
sources = {}
for name in [
    "scripts/qwen3vl_gguf_service.py",
    "labagent/task_agent.py",
    "labagent/providers.py",
    "labagent/readings.py",
]:
    p = ROOT / name
    sources[name] = hashlib.sha256(p.read_bytes()).hexdigest()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
health = {}
for port, path in [
    (8081, "/health"),
    (8031, "/health"),
    (8001, "/api/health"),
    (8035, "/health"),
    (8011, "/health"),
]:
    try:
        with opener.open(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
            health[str(port)] = json.load(response)
    except (OSError, ValueError) as exc:
        health[str(port)] = {"error": str(exc)}
report = {
    "host": platform.node(),
    "machine": platform.machine(),
    "kernel": platform.release(),
    "uid": os.getuid(),
    "aipu": describe("/dev/aipu"),
    "aipu_rw": os.access("/dev/aipu", os.R_OK | os.W_OK),
    "python": run(["python3", "--version"]),
    "extra_python": {
        n: shutil.which(n)
        for n in ["python3.12", "python3.13", "uv", "clang", "cixbuild", "btaipucc"]
    },
    "memory": run(["free", "-b"]),
    "disk": run(["df", "-h", str(ROOT)]),
    "headers": headers,
    "libraries": libraries,
    "libclang": glob.glob("/usr/lib/llvm-*/lib/libclang*.so*")
    + glob.glob("/usr/lib/aarch64-linux-gnu/libclang*.so*"),
    "npu_packages": run(
        [
            "dpkg-query",
            "-W",
            "-f=${Package} ${Version}\n",
            "*aipu*",
            "*cix*npu*",
            "*zhouyi*",
            "*clang*",
        ]
    ),
    "driver_modules": run(["sh", "-c", "lsmod | head -n 12"]),
    "models": models,
    "source_sha256": sources,
    "health": health,
    "app_python": run(
        [
            str(ROOT / ".venv-app/bin/python"),
            "-c",
            'import importlib.util,json,sys;print(json.dumps({"python":sys.version,"modules":{n:bool(importlib.util.find_spec(n)) for n in ["numpy","tokenizers","torch","onnxruntime","pytest"]}}))',
        ]
    ),
    "production_changed": False,
}
Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False), flush=True)
