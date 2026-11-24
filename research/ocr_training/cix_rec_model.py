"""Trainable reconstruction of the *actual* CIX catalog rec.onnx.

The catalog calls this PP-OCRv4, but its graph is ResNet34-vd + 2 BiLSTMs
+ CTC (the older Chinese server recognizer topology). Never substitute a
mobile SVTR model under the same artifact name. Recovery must pass parity.

ResNet-vd structure adapted from PaddleOCR release/2.7 rec_resnet_vd.py.
Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
Licensed under Apache-2.0; see assets/MODEL-LICENSE.txt and the source archive.
"""

from pathlib import Path
import json
import numpy as np
import onnx
from onnx import helper, numpy_helper
from onnx.reference import ReferenceEvaluator
import torch
from torch import nn
from torch.nn import functional as F
from ocr_common import preprocess, characters as characters, decode

ROOT = Path(__file__).resolve().parent


def constants(model):
    return {
        n.output[0]: numpy_helper.to_array(
            next(a.t for a in n.attribute if a.name == "value")
        ).copy()
        for n in model.graph.node
        if n.op_type == "Constant"
    }


def evaluate_constants(model, names):
    producers = {v: n for n in model.graph.node for v in n.output}
    needed = set()

    def visit(value):
        if value in needed:
            return
        needed.add(value)
        for key in producers[value].input:
            if key:
                visit(key)

    for name in names:
        visit(name)
    nodes = [n for n in model.graph.node if any(v in needed for v in n.output)]
    graph = helper.make_graph(
        nodes,
        "constant_weights",
        [],
        [helper.make_tensor_value_info(n, onnx.TensorProto.FLOAT, None) for n in names],
    )
    sub = helper.make_model(graph, opset_imports=model.opset_import)
    return ReferenceEvaluator(sub).run(None, {})


class ConvBN(nn.Module):
    def __init__(self, values, name, stride=(1, 1), relu=True, pool=False):
        super().__init__()
        self.name = name
        w = values[name + "_weights"]
        self.conv = nn.Conv2d(
            w.shape[1],
            w.shape[0],
            w.shape[2],
            stride=1 if pool else stride,
            padding=w.shape[2] // 2,
            bias=False,
        )
        self.bn = nn.BatchNorm2d(w.shape[0], eps=1e-5)
        self.bn_name = "bn" + name[3:]
        with torch.no_grad():
            self.conv.weight.copy_(torch.from_numpy(w))
            for target, suffix in [
                ("weight", "scale"),
                ("bias", "offset"),
                ("running_mean", "mean"),
                ("running_var", "variance"),
            ]:
                getattr(self.bn, target).copy_(
                    torch.from_numpy(values[self.bn_name + "_" + suffix])
                )
        self.relu, self.pool, self.stride = relu, pool, stride

    def forward(self, x):
        if self.pool:
            x = F.avg_pool2d(
                x, self.stride, self.stride, ceil_mode=True, count_include_pad=False
            )
        x = self.bn(self.conv(x))
        return F.relu(x) if self.relu else x


class Block(nn.Module):
    def __init__(self, values, name, down=False, short=False):
        super().__init__()
        stride = (2, 1) if down else (1, 1)
        self.a = ConvBN(values, name + "_branch2a", stride)
        self.b = ConvBN(values, name + "_branch2b", relu=False)
        self.short = (
            ConvBN(values, name + "_branch1", stride, relu=False, pool=down)
            if short
            else nn.Identity()
        )

    def forward(self, x):
        return F.relu(self.b(self.a(x)) + self.short(x))


class CixRecognizer(nn.Module):
    def __init__(self, model_path=ROOT / "assets/rec.onnx"):
        super().__init__()
        graph = onnx.load(str(model_path))
        values = constants(graph)
        self.stem = nn.Sequential(*[ConvBN(values, f"conv1_{i}") for i in (1, 2, 3)])
        self.stages = nn.ModuleList()
        for stage, count in enumerate((3, 4, 6, 3), 2):
            self.stages.append(
                nn.Sequential(
                    *[
                        Block(
                            values,
                            f"res{stage}{chr(97 + i)}",
                            down=stage > 2 and i == 0,
                            short=i == 0,
                        )
                        for i in range(count)
                    ]
                )
            )
        self.rnn = nn.LSTM(512, 256, num_layers=2, bidirectional=True, batch_first=True)
        for layer, node in enumerate(
            n for n in graph.graph.node if n.op_type == "LSTM"
        ):
            W, R, B = evaluate_constants(graph, list(node.input[1:4]))

            # ONNX I,O,F,C -> torch I,F,G,O, with separate input/recurrent biases.
            def gates(array):
                return np.concatenate(
                    np.split(array, 4, axis=0)[0:1]
                    + [np.split(array, 4, axis=0)[i] for i in (2, 3, 1)],
                    axis=0,
                )

            with torch.no_grad():
                for direction, suffix in enumerate(("", "_reverse")):
                    for name, array in [
                        ("weight_ih", W[direction]),
                        ("weight_hh", R[direction]),
                        ("bias_ih", B[direction, :1024]),
                        ("bias_hh", B[direction, 1024:]),
                    ]:
                        getattr(self.rnn, f"{name}_l{layer}{suffix}").copy_(
                            torch.from_numpy(gates(array))
                        )
        self.head = nn.Linear(512, 6625)
        with torch.no_grad():
            self.head.weight.copy_(torch.from_numpy(values["linear_0.w_0"].T.copy()))
            self.head.bias.copy_(torch.from_numpy(values["linear_0.b_0"]))

    def features(self, x):
        x = F.max_pool2d(self.stem(x), 3, 2, 1)
        for stage in self.stages:
            x = stage(x)
        x = F.max_pool2d(x, 2, 2).squeeze(2).transpose(1, 2)
        return self.rnn(x)[0]

    def forward(self, x):
        return self.head(self.features(x))

    def write_onnx(self, path, source=ROOT / "assets/rec.onnx"):
        """Patch only CNN/CTC tensors; keep frozen LSTM and original graph exactly."""
        graph = onnx.load(str(source))
        updates = {}
        for module in self.modules():
            if isinstance(module, ConvBN):
                updates[module.name + "_weights"] = (
                    module.conv.weight.detach().cpu().numpy()
                )
                for target, suffix in [
                    ("weight", "scale"),
                    ("bias", "offset"),
                    ("running_mean", "mean"),
                    ("running_var", "variance"),
                ]:
                    updates[module.bn_name + "_" + suffix] = (
                        getattr(module.bn, target).detach().cpu().numpy()
                    )
        updates["linear_0.w_0"] = self.head.weight.detach().cpu().numpy().T.copy()
        updates["linear_0.b_0"] = self.head.bias.detach().cpu().numpy()
        # Failing closed prevents silently exporting unpatched trainable RNN weights.
        if any(p.requires_grad for p in self.rnn.parameters()):
            raise ValueError("RNN must remain frozen for original-graph export")
        for node in graph.graph.node:
            if node.op_type == "Constant" and node.output[0] in updates:
                tensor = next(a.t for a in node.attribute if a.name == "value")
                tensor.CopyFrom(
                    numpy_helper.from_array(updates.pop(node.output[0]), tensor.name)
                )
        if updates:
            raise ValueError(f"Unmapped tensors: {list(updates)}")
        onnx.checker.check_model(graph)
        onnx.save(graph, str(path))


def check_parity():
    import cv2
    import onnxruntime as ort

    torch.set_num_threads(4)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    model = CixRecognizer().eval().cuda()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    session = ort.InferenceSession(
        str(ROOT / "assets/rec.onnx"), options, providers=["CPUExecutionProvider"]
    )
    probes = [
        np.random.default_rng(i).uniform(-1, 1, (1, 3, 32, 400)).astype("float32")
        for i in range(2)
    ]
    fixture = ROOT.parent / "board_review/2026-11-22/display-readers/display.png"
    probes.append(preprocess(cv2.imread(str(fixture)))[None])
    checks = []
    for x in probes:
        expected = session.run(None, {"x": x})[0]
        with torch.no_grad():
            actual = model(torch.from_numpy(x).cuda()).softmax(-1).cpu().numpy()
        error = float(np.max(np.abs(actual - expected)))
        checks.append(
            {
                "max_abs_error": error,
                "torch_text": decode(actual),
                "onnx_text": decode(expected),
            }
        )
        if error > 2e-4 or decode(actual) != decode(expected):
            raise AssertionError(checks[-1])
    report = {
        "gpu": torch.cuda.get_device_name(0),
        "model": "CIX catalog rec.onnx",
        "checks": checks,
        "passed": True,
    }
    (ROOT / "parity.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    check_parity()
