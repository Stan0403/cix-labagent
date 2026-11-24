"""Repeated high-precision native NPU Qwen projection check using the original weights."""

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np
from aipu_native import NativeGraph

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "vendor/tinygrad-npu-examples/ornith-9b"))
from gguf_read import GGUF

parser = argparse.ArgumentParser()
parser.add_argument("--reload-each", action="store_true")
args = parser.parse_args()
report = {
    "passed": False,
    "production_changed": False,
    "backend": "aipu-native-qwen-fp16-conv",
    "reload_each": args.reload_each,
}
engine = None
try:
    folder = HERE / "real-linear-conv-i0-o0-tilingnone-repeat1-stable0-cache"
    start = time.perf_counter()
    engine = NativeGraph(folder / "compiler-dump/graph_1/aipu.bin")
    report["load_seconds"] = time.perf_counter() - start
    print("Native graph loaded", report["load_seconds"], flush=True)
    g = GGUF(
        "/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent/models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf"
    )
    w = g.f32("blk.0.attn_q.weight").astype(np.float16)
    x = np.random.default_rng(24).normal(0, 0.4, (24, w.shape[1])).astype(np.float16)
    cases = []
    for i in range(5):
        if i and args.reload_each:
            engine.close()
            engine = NativeGraph(folder / "compiler-dump/graph_1/aipu.bin")
        xi = x + np.float16(i * 0.01)
        t = time.perf_counter()
        got = engine.forward([xi])[0].astype(np.float32).reshape(24, w.shape[0])
        seconds = time.perf_counter() - t
        want = xi.astype(np.float32) @ w.astype(np.float32).T
        err = float(np.linalg.norm(got - want) / np.linalg.norm(want))
        cases.append({"seconds": seconds, "relative_l2_error": err})
        print(i, cases[-1], flush=True)
        assert np.isfinite(got).all() and err < 0.005
    report["cases"] = cases
    report["passed"] = True
except Exception as exc:
    report["error"] = str(exc)
    report["traceback"] = traceback.format_exc()
finally:
    if engine:
        engine.close()
    (
        HERE
        / (
            "aipu-native-reload-report.json"
            if args.reload_each
            else "aipu-native-report.json"
        )
    ).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False), flush=True)
raise SystemExit(0 if report["passed"] else 1)
