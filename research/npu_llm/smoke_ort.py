"""Check numerical output and actual NPU node execution using the vendor ORT."""

import json
import os
import time
import traceback
from pathlib import Path

os.environ.setdefault("OPERATOR_PATH", "/usr/share/cix/lib/onnxruntime/operator")
os.environ.setdefault("INTERMIDIATE_PATH", str(Path(__file__).parent / "ort-work"))
Path(os.environ["INTERMIDIATE_PATH"]).mkdir(exist_ok=True)
report = {"passed": False, "production_changed": False}
try:
    import numpy as np
    import onnxruntime as ort

    report["ort_version"] = ort.__version__
    report["available_providers"] = ort.get_available_providers()
    assert "ZhouyiExecutionProvider" in report["available_providers"], report
    root = Path(__file__).parent / "ort-smoke"
    source = str(root / "matrix.onnx")
    x = np.load(root / "input.npy")
    options = ort.SessionOptions()
    options.enable_profiling = True
    options.profile_file_prefix = str(root / "profile")
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    started = time.perf_counter()
    session = ort.InferenceSession(
        source,
        sess_options=options,
        providers=["ZhouyiExecutionProvider", "CPUExecutionProvider"],
    )
    session.disable_fallback()
    report["load_seconds"] = time.perf_counter() - started
    baseline = ort.InferenceSession(source, providers=["CPUExecutionProvider"])
    cases = []
    for i in range(3):
        inputs = {"x": x + np.float32(i * 0.05)}
        start = time.perf_counter()
        got = session.run(None, inputs)[0]
        seconds = time.perf_counter() - start
        want = baseline.run(None, inputs)[0]
        delta = float(np.abs(got - want).max())
        cases.append({"seconds": seconds, "max_abs_error": delta})
        assert np.isfinite(got).all() and delta <= 0.201, delta
    profile = Path(session.end_profiling())
    events = json.loads(profile.read_text())
    providers = [
        e.get("args", {}).get("provider")
        for e in events
        if "kernel_time" in e.get("name", "")
    ]
    report["kernel_providers"] = providers
    assert "ZhouyiExecutionProvider" in providers, (
        "CPU-only execution is not a successful NPU test"
    )
    report["cases"] = cases
    report["profile"] = str(profile)
    report["passed"] = True
except Exception as exc:
    report["error"] = str(exc)
    report["traceback"] = traceback.format_exc()
finally:
    (Path(__file__).parent / "ort-smoke-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
raise SystemExit(0 if report["passed"] else 1)
