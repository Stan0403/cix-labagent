"""Gate real NPU execution and changing-output coherence before loading an LLM."""

import json
import time
import traceback
from pathlib import Path

import bootstrap

report = {"passed": False, "production_services_changed": False}
try:
    report["configuration"] = bootstrap.configure()
    from tinygrad.runtime.support.zhouyi.dev import RawDevice

    raw = RawDevice()
    report["device"] = str(raw)
    report["capabilities"] = {
        "isa": raw.isa_version,
        "cores": raw.core_cnt,
        "tecs": raw.tec_cnt,
    }
    from tinygrad.runtime.support.compiler_zhouyi import toolchain_lib

    toolchain_lib()
    report["compiler_loaded"] = True
    import numpy as np
    from tinygrad import Tensor

    cases = []
    for i in range(4):
        expected = np.arange(32, dtype=np.float32) + i * 7
        start = time.perf_counter()
        got = (Tensor(expected, device="ZHOUYI") * 2 + 3).numpy()
        assert np.array_equal(got, expected * 2 + 3), (i, got.tolist())
        cases.append(
            {"index": i, "seconds": time.perf_counter() - start, "exact": True}
        )
    report["vector_cases"] = cases
    report["passed"] = True
except Exception as exc:
    report["error"] = str(exc)
    report["traceback"] = traceback.format_exc()
finally:
    (Path(__file__).parent / "smoke-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
raise SystemExit(0 if report["passed"] else 1)
