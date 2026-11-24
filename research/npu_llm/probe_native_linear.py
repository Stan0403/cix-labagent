"""Execute the exported floating-point kernel with fresh native jobs, bypassing ORT reuse."""

import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "vendor/tinygrad-npu-examples/ornith-9b"))
import numpy as np
from gguf_read import GGUF

report = {
    "passed": False,
    "production_changed": False,
    "backend": "cix-noe-native-fp16-linear",
}
engine = None
try:
    from libnoe import NOE_TENSOR_TYPE_OUTPUT
    from NOE_Engine import EngineInfer

    folder = HERE / "real-linear-conv-i0-o0-tilingnone-repeat1-stable0-cache"
    binary = folder / "compiler-dump/graph_1/aipu.bin"
    start = time.perf_counter()
    engine = EngineInfer(str(binary))
    report["load_seconds"] = time.perf_counter() - start
    report["tensors"] = {
        "input_types": [str(d.data_type) for d in engine.in_tensor_desc],
        "input_bytes": [d.size for d in engine.in_tensor_desc],
        "output_types": [str(d.data_type) for d in engine.out_tensor_desc],
    }
    assert engine.input_type == [np.float16] and engine.output_type == [np.float16], (
        report
    )
    g = GGUF(
        "/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent/models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf"
    )
    w = g.f32("blk.0.attn_q.weight").astype(np.float16)
    x = np.random.default_rng(24).normal(0, 0.4, (24, w.shape[1])).astype(np.float16)
    cases = []
    for i in range(5):
        xi = x + np.float16(i * 0.01)
        assert xi.nbytes == engine.in_tensor_desc[0].size
        started = time.perf_counter()
        engine._create_job()
        try:
            assert engine.npu.noe_load_tensor(engine.job_id, 0, xi.tobytes()) == 0
            assert engine.npu.noe_job_infer_sync(engine.job_id, 5000) == 0
            ret, data = engine.npu.noe_get_tensor(
                engine.job_id, NOE_TENSOR_TYPE_OUTPUT, 0
            )
            assert ret == 0
            got = (
                np.frombuffer(data, np.float16)
                .astype(np.float32)
                .reshape(24, w.shape[0])
            )
        finally:
            engine.npu.noe_clean_job(engine.job_id)
        seconds = time.perf_counter() - started
        want = xi.astype(np.float32) @ w.astype(np.float32).T
        error = float(np.linalg.norm(got - want) / np.linalg.norm(want))
        cases.append({"seconds": seconds, "relative_l2_error": error})
        print("Native run", i, cases[-1], flush=True)
        assert np.isfinite(got).all() and error < 0.005
    report["cases"] = cases
    report["passed"] = True
except Exception as exc:
    report["error"] = str(exc)
    report["traceback"] = traceback.format_exc()
finally:
    if engine:
        engine.clean()
    (HERE / "native-linear-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
raise SystemExit(0 if report["passed"] else 1)
