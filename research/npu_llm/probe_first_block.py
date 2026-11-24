"""Isolated Qwen3-4B first-block prefill/decode comparison on real prompt tokens.

Only blk.0.attn_q is offloaded. This does not generate text or run all 36 blocks.
Equations follow the Qwen team's Transformers Qwen3 implementation; the GGUF
reader is from tinygrad-npu-examples (see NOTICE.md).
"""

import argparse
import hashlib
import json
import sys
import time
import traceback
import urllib.request
from pathlib import Path

import numpy as np
from aipu_native import ReloadingLinear

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor/tinygrad-npu-examples/ornith-9b"))
from gguf_read import GGML, GGUF, dequant_q4_k, dequant_q6_k

MODEL = "/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent/models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf"


def rms(x, weight, epsilon):
    return x / np.sqrt(np.mean(x * x, axis=-1, keepdims=True) + epsilon) * weight


class FirstBlock:
    def __init__(self):
        self.gguf = GGUF(MODEL)
        m = self.gguf.meta
        assert m["general.architecture"] == "qwen3"
        self.heads = m["qwen3.attention.head_count"]
        self.kv_heads = m["qwen3.attention.head_count_kv"]
        self.dim = m["qwen3.attention.key_length"]
        self.theta = m["qwen3.rope.freq_base"]
        self.eps = m["qwen3.attention.layer_norm_rms_epsilon"]
        self.w = {
            name: self.gguf.f32("blk.0." + name + ".weight")
            for name in (
                "attn_norm",
                "attn_q",
                "attn_k",
                "attn_v",
                "attn_q_norm",
                "attn_k_norm",
                "attn_output",
                "ffn_norm",
                "ffn_gate",
                "ffn_up",
                "ffn_down",
            )
        }

    def embed(self, ids):
        kind, dims, raw = self.gguf.raw("token_embd.weight")
        assert kind in {"Q4_K", "Q6_K"}, kind
        size = GGML[self.gguf.tensors["token_embd.weight"][1]][2]
        blocks = raw.reshape(-1, dims[0] // 256, size)[ids]
        fn = dequant_q4_k if kind == "Q4_K" else dequant_q6_k
        return fn(blocks.reshape(-1, size)).reshape(len(ids), dims[0])

    def rope(self, x, positions):
        angles = positions[:, None] * self.theta ** (
            -np.arange(0, self.dim, 2, dtype=np.float64) / self.dim
        )
        cosine, sine = (
            np.cos(angles).astype(np.float32)[:, None],
            np.sin(angles).astype(np.float32)[:, None],
        )
        left, right = np.split(x, 2, axis=-1)
        return np.concatenate(
            (left * cosine - right * sine, left * sine + right * cosine), axis=-1
        )

    def forward(self, x, projection=None, cache=None):
        w = self.w
        n = len(x)
        past = 0 if cache is None else len(cache[0])
        pos = np.arange(past, past + n)
        norm = rms(x, w["attn_norm"], self.eps)
        q_start = time.perf_counter()
        q = norm @ w["attn_q"].T if projection is None else projection(norm)
        self.last_q_seconds = time.perf_counter() - q_start
        q = self.rope(
            rms(q.reshape(n, self.heads, self.dim), w["attn_q_norm"], self.eps), pos
        )
        k = (norm @ w["attn_k"].T).reshape(n, self.kv_heads, self.dim)
        k = self.rope(rms(k, w["attn_k_norm"], self.eps), pos)
        v = (norm @ w["attn_v"].T).reshape(n, self.kv_heads, self.dim)
        if cache is not None:
            k, v = np.concatenate((cache[0], k)), np.concatenate((cache[1], v))
        groups = self.heads // self.kv_heads
        grouped = q.reshape(n, self.kv_heads, groups, self.dim)
        scores = np.einsum("nkgd,tkd->nkgt", grouped, k) / np.sqrt(np.float32(self.dim))
        allowed = np.arange(len(k))[None] <= pos[:, None]
        scores = np.where(allowed[:, None, None], scores, -np.inf)
        prob = np.exp(scores - scores.max(axis=-1, keepdims=True))
        prob /= prob.sum(axis=-1, keepdims=True)
        attended = np.einsum("nkgt,tkd->nkgd", prob, v).reshape(
            n, self.heads * self.dim
        )
        residual = x + attended @ w["attn_output"].T
        norm = rms(residual, w["ffn_norm"], self.eps)
        gate = norm @ w["ffn_gate"].T
        silu = gate / (1 + np.exp(-np.clip(gate, -80, 80)))
        result = residual + (silu * (norm @ w["ffn_up"].T)) @ w["ffn_down"].T
        return result, (k, v)


def tokenize(text):
    request = urllib.request.Request(
        "http://127.0.0.1:8081/tokenize",
        data=json.dumps({"content": text, "add_special": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        return json.load(response)["tokens"]


def timed(fn, *args, **kwargs):
    begin = time.perf_counter()
    value = fn(*args, **kwargs)
    return value, time.perf_counter() - begin


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quantized", action="store_true")
    parser.add_argument("--smooth", action="store_true")
    args = parser.parse_args()
    report = {
        "passed": False,
        "production_changed": False,
        "scope": "one complete transformer block; only its Q projection on NPU; not full text generation",
        "cases": [],
        "cpu_reference": "NumPy float32 GGUF dequantization, not llama.cpp timing",
    }
    try:
        model = FirstBlock()
        folder = HERE / (
            "real-linear-qhost-i0-o0-tilingnone-repeat1-stable0-real-cache"
            if args.quantized
            else "real-linear-conv-i0-o0-tilingnone-repeat1-stable0-cache"
        )
        single = HERE / (
            "real-linear-qhost-i0-o0-tilingnone-repeat1-stable0-rows1-real-cache"
            if args.quantized
            else "real-linear-conv-i0-o0-tilingnone-repeat1-stable0-rows1-cache"
        )
        if args.smooth:
            assert args.quantized
            folder = folder.with_name(folder.name.replace("-cache", "-sq50-cache"))
            single = single.with_name(single.name.replace("-cache", "-sq50-cache"))

        def binary(p):
            return p / "compiler-dump/graph_1/aipu.bin"

        projection = {
            n: ReloadingLinear(
                binary(p),
                n,
                2560,
                4096,
                dtype=np.float32 if args.quantized else np.float16,
                scale=np.load(p / "output-scales.npy") if args.quantized else None,
                input_divisors=np.load(p / "input-divisors.npy")
                if args.smooth
                else None,
            )
            for n, p in [(24, folder), (1, single)]
        }
        report["projection_mode"] = (
            "W8A8-NPU-host-channel-scale" if args.quantized else "FP16-NPU-conv"
        )
        report["smoothquant_alpha"] = 0.5 if args.smooth else None
        report["binary_sha256"] = {
            str(n): hashlib.sha256(binary(p).read_bytes()).hexdigest()
            for n, p in [(24, folder), (1, single)]
        }
        prompts = [
            "你是实验助手。电池、万用表、红黑表笔已经准备完毕，直流电压20V档已由操作者确认。接下来应该执行哪个步骤？请只给出一个行动。",
            "你是实验助手。现在两次电压读数分别是1.51V和1.50V，间隔超过一秒，图像来源不同，量程均为20V。请判断是否可以保存测量结果。",
            "请将实验指导材料整理为操作计划：先断开电源，再确认万用表档位，连接表笔并读取电池电压，最后保存结果。不要代替操作者确认现场接线。",
        ]
        for text in prompts:
            ids = tokenize(text)
            assert len(ids) >= 25
            ids = ids[-25:]
            x = model.embed(ids[:24])
            (cpu, kv), cpu_time = timed(model.forward, x)
            cpu_prefill_q = model.last_q_seconds
            (hybrid, npu_kv), npu_time = timed(model.forward, x, projection[24])
            hybrid_prefill_q = model.last_q_seconds
            # Teacher-forced decode uses the next real prompt token and the KV
            # cache from that same prefill. No generated-token claim is made.
            decode = model.embed(ids[24:])
            (cpu_d, _), cpu_decode = timed(model.forward, decode, cache=kv)
            cpu_decode_q = model.last_q_seconds
            (npu_d, _), npu_decode = timed(model.forward, decode, projection[1], npu_kv)
            hybrid_decode_q = model.last_q_seconds
            case = {
                "prompt_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "token_ids": ids,
                "cpu_prefill_seconds": cpu_time,
                "hybrid_prefill_seconds": npu_time,
                "cpu_decode_seconds": cpu_decode,
                "hybrid_decode_seconds": npu_decode,
                "cpu_prefill_q_seconds": cpu_prefill_q,
                "hybrid_prefill_q_seconds": hybrid_prefill_q,
                "cpu_decode_q_seconds": cpu_decode_q,
                "hybrid_decode_q_seconds": hybrid_decode_q,
                "prefill_relative_l2": float(
                    np.linalg.norm(cpu - hybrid) / np.linalg.norm(cpu)
                ),
                "decode_relative_l2": float(
                    np.linalg.norm(cpu_d - npu_d) / np.linalg.norm(cpu_d)
                ),
            }
            report["cases"].append(case)
            print(json.dumps(case), flush=True)
            assert np.isfinite(hybrid).all() and np.isfinite(npu_d).all()
        report["passed"] = all(
            c["prefill_relative_l2"] < 0.005 and c["decode_relative_l2"] < 0.005
            for c in report["cases"]
        )
        if not report["passed"]:
            report["error"] = (
                "First-block relative L2 exceeds the 0.5% experimental numerical gate; full-model quality untested"
            )
    except Exception as exc:  # noqa: BLE001 -- report a failed hardware gate
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
    (
        HERE
        / (
            "first-block-smooth-report.json"
            if args.smooth
            else "first-block-quantized-report.json"
            if args.quantized
            else "first-block-report.json"
        )
    ).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
