"""Create a quantized matrix graph to verify the vendor Zhouyi EP, not a CPU fallback."""

from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto as T
from onnx import helper as h
from onnx import numpy_helper as nh

folder = Path(__file__).parent / "2026-11-24/ort-smoke"
folder.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(24)
w = rng.integers(-30, 31, (256, 256), dtype=np.int8)
initializers = [
    nh.from_array(w, "w"),
    nh.from_array(np.float32(0.03), "xs"),
    nh.from_array(np.float32(0.02), "ws"),
    nh.from_array(np.float32(0.10), "ys"),
    nh.from_array(np.int8(0), "zero"),
]
nodes = [
    h.make_node("QuantizeLinear", ["x", "xs", "zero"], ["xq"]),
    h.make_node(
        "QLinearMatMul",
        ["xq", "xs", "zero", "w", "ws", "zero", "ys", "zero"],
        ["yq"],
        name="npu_matmul",
    ),
    h.make_node("DequantizeLinear", ["yq", "ys", "zero"], ["y"]),
]
model = h.make_model(
    h.make_graph(
        nodes,
        "npu-matrix-smoke",
        [h.make_tensor_value_info("x", T.FLOAT, [24, 256])],
        [h.make_tensor_value_info("y", T.FLOAT, [24, 256])],
        initializers,
    ),
    opset_imports=[h.make_opsetid("", 17)],
    ir_version=10,
)
onnx.checker.check_model(model)
onnx.save(model, folder / "matrix.onnx")
np.save(folder / "input.npy", rng.uniform(-2, 2, (24, 256)).astype(np.float32))
print(folder)
