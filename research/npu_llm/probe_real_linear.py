"""Test exact Qwen3 GGUF linear weights as FP16 MatMul and equivalent 1x1 Conv."""

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor/tinygrad-npu-examples/ornith-9b"))
os.environ.setdefault("OPERATOR_PATH", "/usr/share/cix/lib/onnxruntime/operator")
os.environ.setdefault("INTERMIDIATE_PATH", str(HERE / "ort-work"))

import numpy as np
import onnx
import onnxruntime as ort
from gguf_read import GGUF
from onnx import TensorProto as T
from onnx import helper as h
from onnx import numpy_helper as nh

MODEL = "/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent/models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf"
parser = argparse.ArgumentParser()
parser.add_argument(
    "--mode",
    choices=["matmul", "conv", "qdq", "qlinear", "qconv", "qscaled", "qhost"],
    required=True,
)
parser.add_argument("--tensor", default="blk.0.attn_q.weight")
parser.add_argument("--dtype", choices=["float16", "float32"], default="float16")
parser.add_argument("--tile", type=int, default=0)
parser.add_argument("--input-tile", type=int, default=0)
parser.add_argument("--output-tile", type=int, default=0)
parser.add_argument("--repeat", type=int, default=3)
parser.add_argument("--rows", type=int, default=24)
parser.add_argument("--input-npy")
parser.add_argument("--calibration-npy")
parser.add_argument("--smooth-alpha", type=float)
parser.add_argument("--stable-io", action="store_true")
parser.add_argument("--cache", action="store_true")
args = parser.parse_args()
folder = HERE / (
    "real-linear-" + args.mode + ("-f32" if args.dtype == "float32" else "")
)
folder = folder.with_name(
    folder.name
    + f"-i{args.input_tile or args.tile}-o{args.output_tile or args.tile}-tiling{os.environ.get('TILING_METHOD', 'fps')}"
)
folder = folder.with_name(
    folder.name + f"-repeat{args.repeat}-stable{int(args.stable_io)}"
)
if args.rows != 24:
    folder = folder.with_name(folder.name + f"-rows{args.rows}")
if args.input_npy:
    folder = folder.with_name(folder.name + "-real")
if args.smooth_alpha is not None:
    assert 0 <= args.smooth_alpha <= 1 and args.mode == "qhost"
    folder = folder.with_name(folder.name + f"-sq{round(args.smooth_alpha * 100)}")
if args.cache:
    folder = folder.with_name(folder.name + "-cache")
folder.mkdir(exist_ok=True)
if args.cache:
    work = folder / "compiler-dump"
    work.mkdir(exist_ok=True)
    os.environ["DUMP_FILE"] = "on"
    os.environ["INTERMIDIATE_PATH"] = str(work)
report = {
    "passed": False,
    "mode": args.mode,
    "tensor": args.tensor,
    "production_changed": False,
}
session = None
report["cases"] = []
try:
    g = GGUF(MODEL)
    assert g.meta["general.architecture"] == "qwen3"
    report["metadata"] = {
        k: v
        for k, v in g.meta.items()
        if k.startswith("qwen3.") and not isinstance(v, list)
    }
    dtype = np.float16 if args.dtype == "float16" else np.float32
    onnx_dtype = T.FLOAT16 if args.dtype == "float16" else T.FLOAT
    w = g.f32(args.tensor).astype(dtype)
    if args.tile or args.input_tile or args.output_tile:
        w = w[
            : args.output_tile or args.tile or None,
            : args.input_tile or args.tile or None,
        ].copy()
    n, k = w.shape
    rows = args.rows
    assert rows > 0
    rng = np.random.default_rng(24)
    x = rng.normal(0, 0.4, (rows, k)).astype(dtype)
    report["shape"] = [rows, k, n]
    if args.input_npy:
        x = np.load(args.input_npy).astype(dtype)
        assert x.shape == (rows, k)
        report["input_sha256"] = hashlib.sha256(
            Path(args.input_npy).read_bytes()
        ).hexdigest()
    calibration = (
        np.load(args.calibration_npy).astype(np.float32)
        if args.calibration_npy
        else x.astype(np.float32)
    )
    if args.calibration_npy:
        report["calibration_sha256"] = hashlib.sha256(
            Path(args.calibration_npy).read_bytes()
        ).hexdigest()
    if args.mode in {"qlinear", "qconv", "qscaled", "qhost"}:
        w = g.f32(args.tensor)[:n, :k].copy()
        onnx_dtype = T.FLOAT
        x = x.astype(np.float32)
        if args.smooth_alpha is not None:
            actmax = np.maximum(np.abs(calibration).max(axis=0), 1e-8)
            weightmax = np.maximum(np.abs(w).max(axis=0), 1e-8)
            divisors = np.clip(
                actmax**args.smooth_alpha / weightmax ** (1 - args.smooth_alpha),
                1e-5,
                1e5,
            ).astype(np.float32)
            x = x / divisors
            calibration = calibration / divisors
            w *= divisors
            np.save(folder / "input-divisors.npy", divisors)
            report["smoothquant_alpha"] = args.smooth_alpha
        xs = np.float32(np.abs(calibration).max() / 110)
        ws = (
            np.maximum(np.abs(w).max(axis=1) / 127, 1e-9).astype(np.float32)
            if args.mode in {"qconv", "qscaled", "qhost"}
            else np.float32(np.abs(w).max() / 127)
        )
        ys = np.float32(np.abs(calibration @ w.T).max() / 105)
        wq = (
            np.rint(w / ws[:, None]).clip(-127, 127).astype(np.int8)
            if args.mode in {"qconv", "qscaled", "qhost"}
            else np.rint(w / ws).clip(-127, 127).astype(np.int8)
        )
        if args.mode in {"qscaled", "qhost"}:
            ys = np.float32(np.abs(calibration @ wq.astype(np.float32).T).max() / 105)
            np.save(folder / "output-scales.npy", ws)
        wq = wq[:, :, None, None] if args.mode == "qconv" else wq.T.copy()
        initializers = [
            nh.from_array(wq, "wq"),
            nh.from_array(ws, "ws"),
            nh.from_array(xs, "xs"),
            nh.from_array(ys, "ys"),
            nh.from_array(np.int8(0), "zero"),
        ]
        if args.mode == "qconv":
            initializers.extend(
                [
                    nh.from_array(np.zeros(n, np.int8), "wz"),
                    nh.from_array(np.array([1, k, 1, rows], np.int64), "xin_shape"),
                    nh.from_array(np.array([n, rows], np.int64), "yout_shape"),
                ]
            )
            nodes = [
                h.make_node("Transpose", ["x"], ["xt"], perm=[1, 0]),
                h.make_node("Reshape", ["xt", "xin_shape"], ["xc"]),
                h.make_node("QuantizeLinear", ["xc", "xs", "zero"], ["xq"]),
                h.make_node(
                    "QLinearConv",
                    ["xq", "xs", "zero", "wq", "ws", "wz", "ys", "zero"],
                    ["yq"],
                    kernel_shape=[1, 1],
                    name="qwen_linear_int8_conv",
                ),
                h.make_node("DequantizeLinear", ["yq", "ys", "zero"], ["yc"]),
                h.make_node("Reshape", ["yc", "yout_shape"], ["yt"]),
                h.make_node("Transpose", ["yt"], ["y"], perm=[1, 0]),
            ]
        elif args.mode in {"qscaled", "qhost"}:
            initializers.extend([nh.from_array(np.float32(1), "unit")])
            nodes = [
                h.make_node("QuantizeLinear", ["x", "xs", "zero"], ["xq"]),
                h.make_node(
                    "QLinearMatMul",
                    ["xq", "xs", "zero", "wq", "unit", "zero", "ys", "zero"],
                    ["yq"],
                    name="qwen_linear_int8",
                ),
                h.make_node(
                    "DequantizeLinear",
                    ["yq", "ys", "zero"],
                    ["y" if args.mode == "qhost" else "ydot"],
                ),
            ]
            if args.mode == "qscaled":
                nodes.append(
                    h.make_node(
                        "Mul",
                        ["ydot", "ws"],
                        ["y"],
                        name="restore_per_channel_weight_scale",
                    )
                )
        else:
            nodes = [
                h.make_node("QuantizeLinear", ["x", "xs", "zero"], ["xq"]),
                h.make_node(
                    "QLinearMatMul",
                    ["xq", "xs", "zero", "wq", "ws", "zero", "ys", "zero"],
                    ["yq"],
                    name="qwen_linear_int8",
                ),
                h.make_node("DequantizeLinear", ["yq", "ys", "zero"], ["y"]),
            ]
        report["quantization"] = {
            "input_scale": float(xs),
            "weight_scale_min": float(np.min(ws)),
            "weight_scale_max": float(np.max(ws)),
            "per_output_channel": args.mode in {"qconv", "qscaled", "qhost"},
            "output_scale": float(ys),
            "calibration": "real first-block activations, not full-model calibration"
            if args.calibration_npy
            else "synthetic activation only, not production calibration",
        }
    elif args.mode == "qdq":
        w = g.f32(args.tensor)[:n, :k].copy()
        onnx_dtype = T.FLOAT
        x = x.astype(np.float32)
        ws = np.maximum(np.abs(w).max(axis=1) / 127, 1e-8).astype(np.float32)
        wq = np.rint(w / ws[:, None]).clip(-127, 127).astype(np.int8).T.copy()
        initializers = [
            nh.from_array(wq, "wq"),
            nh.from_array(ws, "ws"),
            nh.from_array(np.zeros(n, np.int8), "wz"),
            nh.from_array(np.float32(0.02), "xs"),
            nh.from_array(np.int8(0), "xz"),
        ]
        nodes = [
            h.make_node("QuantizeLinear", ["x", "xs", "xz"], ["xq"]),
            h.make_node("DequantizeLinear", ["xq", "xs", "xz"], ["xd"]),
            h.make_node("DequantizeLinear", ["wq", "ws", "wz"], ["wd"], axis=1),
            h.make_node("MatMul", ["xd", "wd"], ["y"], name="qwen_linear_qdq"),
        ]
    elif args.mode == "matmul":
        nodes = [h.make_node("MatMul", ["x", "w"], ["y"], name="qwen_linear")]
        initializers = [nh.from_array(w.T.copy(), "w")]
    else:
        initializers = [
            nh.from_array(w[:, :, None, None], "w"),
            nh.from_array(np.array([1, k, 1, rows], np.int64), "xin_shape"),
            nh.from_array(np.array([n, rows], np.int64), "yout_shape"),
        ]
        nodes = [
            h.make_node("Transpose", ["x"], ["xt"], perm=[1, 0]),
            h.make_node("Reshape", ["xt", "xin_shape"], ["xc"]),
            h.make_node(
                "Conv",
                ["xc", "w"],
                ["yc"],
                kernel_shape=[1, 1],
                name="qwen_linear_conv",
            ),
            h.make_node("Reshape", ["yc", "yout_shape"], ["yt"]),
            h.make_node("Transpose", ["yt"], ["y"], perm=[1, 0]),
        ]
    model = h.make_model(
        h.make_graph(
            nodes,
            "qwen-real-linear",
            [h.make_tensor_value_info("x", onnx_dtype, [rows, k])],
            [h.make_tensor_value_info("y", onnx_dtype, [rows, n])],
            initializers,
        ),
        opset_imports=[h.make_opsetid("", 17)],
        ir_version=10,
    )
    onnx.checker.check_model(model)
    path = folder / "linear.onnx"
    onnx.save(model, path)
    print("Exported", args.mode, [rows, k, n], flush=True)
    options = ort.SessionOptions()
    options.enable_profiling = True
    options.profile_file_prefix = str(folder / "profile")
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    options.log_severity_level = 2
    if args.cache:
        options.add_session_config_entry("ep.context_enable", "1")
        options.add_session_config_entry("ep.context_embed_mode", "0")
        options.add_session_config_entry(
            "ep.context_file_path", str(folder / f"context-{time.time_ns()}.onnx")
        )
    start = time.perf_counter()
    print("Creating vendor NPU session", flush=True)
    session = ort.InferenceSession(
        str(path),
        sess_options=options,
        providers=["ZhouyiExecutionProvider", "CPUExecutionProvider"],
    )
    session.disable_fallback()
    report["load_seconds"] = time.perf_counter() - start
    print("Session ready", report["load_seconds"], flush=True)
    cases = report["cases"]
    cpu_session = (
        ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        if args.mode == "qconv"
        else None
    )
    stable_input = x.copy()
    for i in range(args.repeat):
        print("Inference", i, flush=True)
        if args.stable_io:
            stable_input[:] = x + dtype(i * 0.01)
            xi = stable_input
        else:
            xi = x + dtype(i * 0.01)
        start = time.perf_counter()
        got = session.run(None, {"x": xi})[0].astype(np.float32)
        if args.mode == "qhost":
            got *= ws
        seconds = time.perf_counter() - start
        want = xi.astype(np.float32) @ w.astype(np.float32).T
        error = float(np.linalg.norm(got - want) / max(np.linalg.norm(want), 1e-12))
        cases.append(
            {
                "seconds": seconds,
                "relative_l2_error": error,
                "max_abs_error": float(np.abs(got - want).max()),
            }
        )
        if cpu_session is not None:
            cpu_quantized = cpu_session.run(None, {"x": xi})[0]
            cases[-1]["cpu_quantized_relative_l2"] = float(
                np.linalg.norm(cpu_quantized - want) / np.linalg.norm(want)
            )
            cases[-1]["npu_vs_cpu_quantized_relative_l2"] = float(
                np.linalg.norm(got - cpu_quantized) / np.linalg.norm(cpu_quantized)
            )
        print("Measured", json.dumps(cases[-1]), flush=True)
        assert np.isfinite(got).all() and error < (
            0.05
            if args.mode in {"qdq", "qlinear", "qconv", "qscaled", "qhost"}
            else 0.005
        ), cases[-1]
    report["passed"] = True
except Exception as exc:
    report["error"] = str(exc)
    report["traceback"] = traceback.format_exc()
finally:
    if session is not None:
        try:
            profile = Path(session.end_profiling())
            events = json.loads(profile.read_text())
            providers = [
                e.get("args", {}).get("provider")
                for e in events
                if "kernel_time" in e.get("name", "")
            ]
            report["kernel_providers"] = providers
            if "ZhouyiExecutionProvider" not in providers and report["passed"]:
                report["passed"] = False
                report["error"] = "No actual NPU execution"
        except Exception as exc:
            report["passed"] = False
            report["profile_error"] = str(exc)
    (folder / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
raise SystemExit(0 if report["passed"] else 1)
