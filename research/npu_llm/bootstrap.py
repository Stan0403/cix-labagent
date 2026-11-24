"""Configure only the isolated adapter for the installed CIX SDK naming layout."""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor/tinygrad"))
sys.path.insert(0, str(HERE / "vendor/tinygrad-npu-examples/ornith-9b"))


def configure():
    os.environ.setdefault("DEV", "ZHOUYI")
    os.environ.setdefault("ZHOUYI_TEXT_GM", "0")
    os.environ.setdefault("AIPU_JOB_TIMEOUT", "4")
    header = Path(
        "/usr/src/cix-drivers-dkms-0.2.1-4/npu/driver/armchina-npu/include/armchina_aipu.h"
    )
    if header.exists():
        os.environ.setdefault("ZHOUYI_AIPU_HEADER", str(header))
    clang = HERE / ".venv/lib/python3.11/site-packages/clang/native/libclang.so"
    if clang.exists():
        os.environ.setdefault("LIBCLANG_PATH", str(clang))
    import tinygrad.runtime.support.compiler_zhouyi as compiler

    # Same compiler entry points, but the installed 2025 libraries use older names.
    directory = Path(os.environ.get("ZHOUYI_TOOLCHAIN_DIR", compiler.TOOLCHAIN_DIR))
    if (directory / "libaipu_buildingtool.so").exists() and (
        directory / "libaiputoolchain_core.so"
    ).exists():
        compiler.TOOLCHAIN_LIBS = (
            "libaipu_buildingtool.so",
            "libaiputoolchain_core.so",
            "libaiputoolchain.so",
        )
    from tinygrad.runtime.autogen import aipu
    from tinygrad.runtime.support.zhouyi import dev

    legacy = not hasattr(aipu.struct_aipu_job_desc, "asid0_base")
    if legacy and not getattr(dev, "_codex_legacy_grid_end", False):
        original = dev.build_group_tcbs

        def stock_group_tcbs(*args, **kwargs):
            kwargs["grid_end"] = True
            return original(*args, **kwargs)

        dev.build_group_tcbs = stock_group_tcbs
        dev._codex_legacy_grid_end = True
    return {
        "header": os.environ.get("ZHOUYI_AIPU_HEADER"),
        "toolchain": str(directory),
        "libraries": compiler.TOOLCHAIN_LIBS,
        "libclang": os.environ.get("LIBCLANG_PATH"),
        "text_gm": os.environ["ZHOUYI_TEXT_GM"],
        "stock_kmd_grid_end": legacy,
    }
