"""Minimal native runtime for compiler-exported AIPU ELF graphs.

ABI: Arm-China/Compass_NPU_Driver, Linux/driver/umd/include/standard_api.h.
No kernel/module modifications; each forward gets a fresh job and finite timeout.
"""

import ctypes as C
from pathlib import Path

import numpy as np


class TensorDescriptor(C.Structure):
    _fields_ = [
        ("id", C.c_uint32),
        ("size", C.c_uint32),
        ("scale", C.c_float),
        ("zero_point", C.c_int32),
        ("data_type", C.c_int32),
    ]


class NativeGraph:
    def __init__(self, path):
        self.lib = C.CDLL(
            "/usr/share/cix/lib/onnxruntime/libaipu_driver.so", mode=C.RTLD_LOCAL
        )
        pointer, u64, u32 = C.c_void_p, C.c_uint64, C.c_uint32
        signatures = {
            "aipu_init_context": [C.POINTER(pointer)],
            "aipu_deinit_context": [pointer],
            "aipu_load_graph": [pointer, C.c_char_p, C.POINTER(u64), pointer],
            "aipu_unload_graph": [pointer, u64],
            "aipu_get_tensor_count": [pointer, u64, u32, C.POINTER(u32)],
            "aipu_get_tensor_descriptor": [
                pointer,
                u64,
                u32,
                u32,
                C.POINTER(TensorDescriptor),
            ],
            "aipu_create_job": [pointer, u64, C.POINTER(u64), pointer],
            "aipu_clean_job": [pointer, u64],
            "aipu_finish_job": [pointer, u64, C.c_int32],
            "aipu_load_tensor": [pointer, u64, u32, pointer],
            "aipu_get_tensor": [pointer, u64, u32, u32, pointer],
        }
        for name, args in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes = args
            fn.restype = C.c_int32
        self.ctx, self.graph = pointer(), u64()
        self._loaded = False
        try:
            self._check(self.lib.aipu_init_context(C.byref(self.ctx)), "init")
            self._check(
                self.lib.aipu_load_graph(
                    self.ctx, str(Path(path)).encode(), C.byref(self.graph), None
                ),
                "load",
            )
            self._loaded = True
            self.inputs, self.outputs = self._descriptors(0), self._descriptors(1)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _check(status, operation):
        if status:
            raise RuntimeError(f"AIPU {operation} failed: status {status}")

    def _descriptors(self, kind):
        count = C.c_uint32()
        self._check(
            self.lib.aipu_get_tensor_count(self.ctx, self.graph, kind, C.byref(count)),
            "tensor count",
        )
        assert 0 < count.value < 16, count.value
        result = []
        for i in range(count.value):
            d = TensorDescriptor()
            self._check(
                self.lib.aipu_get_tensor_descriptor(
                    self.ctx, self.graph, kind, i, C.byref(d)
                ),
                "tensor descriptor",
            )
            result.append(d)
        return result

    def forward(self, inputs, timeout_ms=5000):
        if not self._loaded:
            raise RuntimeError("Graph is closed")
        if len(inputs) != len(self.inputs):
            raise ValueError("Incorrect input count")
        arrays = [np.ascontiguousarray(x) for x in inputs]
        dtypes = {10: np.dtype(np.float16), 11: np.dtype(np.float32)}
        if any(
            x.nbytes != d.size or x.dtype != dtypes.get(d.data_type)
            for x, d in zip(arrays, self.inputs)
        ):
            raise ValueError("Only exact-size FP16/FP32 input buffers are supported")
        job = C.c_uint64()
        # This installed UMD requires a non-null default create-job config.
        # All documented fields default to zero; reserve space for both its
        # older layout and newer append-only fields, without enabling options.
        config = (C.c_uint64 * 16)()
        self._check(
            self.lib.aipu_create_job(
                self.ctx, self.graph, C.byref(job), C.byref(config)
            ),
            "create job",
        )
        try:
            for i, x in enumerate(arrays):
                self._check(
                    self.lib.aipu_load_tensor(self.ctx, job, i, x.ctypes.data),
                    "load tensor",
                )
            self._check(
                self.lib.aipu_finish_job(self.ctx, job, timeout_ms), "finish job"
            )
            outputs = []
            for i, d in enumerate(self.outputs):
                dtype = dtypes[d.data_type]
                assert d.size % dtype.itemsize == 0
                x = np.empty(d.size // dtype.itemsize, dtype)
                self._check(
                    self.lib.aipu_get_tensor(self.ctx, job, 1, i, x.ctypes.data),
                    "get tensor",
                )
                outputs.append(x)
            return outputs
        finally:
            self._check(self.lib.aipu_clean_job(self.ctx, job), "clean job")

    def close(self):
        try:
            if self._loaded:
                self._loaded = False
                self._check(self.lib.aipu_unload_graph(self.ctx, self.graph), "unload")
        finally:
            if self.ctx:
                context, self.ctx = self.ctx, C.c_void_p()
                self._check(self.lib.aipu_deinit_context(context), "deinit")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class ReloadingLinear:
    """FP16 projection prototype; reload context to avoid stock UMD replay timeout.

    This is deliberately not attached to the production llama.cpp service.
    The binary must have been compiled for exactly (rows, input_size, output_size).
    """

    def __init__(
        self,
        path,
        rows,
        input_size,
        output_size,
        dtype=np.float16,
        scale=None,
        input_divisors=None,
    ):
        self.path = Path(path)
        self.shape = (rows, input_size)
        self.output_shape = (rows, output_size)
        self.dtype = np.dtype(dtype)
        self.scale = scale
        self.input_divisors = input_divisors

    def __call__(self, x):
        if x.shape != self.shape:
            raise ValueError(f"Expected {self.shape}, got {x.shape}")
        if self.input_divisors is not None:
            x = x / self.input_divisors
        with NativeGraph(self.path) as engine:
            got = engine.forward([np.ascontiguousarray(x, dtype=self.dtype)])[0]
        got = got.reshape(self.output_shape).astype(np.float32)
        return got if self.scale is None else got * self.scale
