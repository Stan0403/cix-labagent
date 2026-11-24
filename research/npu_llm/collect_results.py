"""Archive only this isolated experiment's results and selected compiled graphs."""

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
files = {p: p.relative_to(ROOT).as_posix() for p in ROOT.glob("*report.json")}
for name in ['adaptation-summary.json', '2026-11-24/source-provenance.json']:
    path = ROOT / name
    if path.is_file():
        files[path] = name
for folder in ROOT.glob("real-linear-*"):
    for name in ["report.json", "output-scales.npy", "input-divisors.npy"]:
        path = folder / name
        if path.is_file():
            files[path] = path.relative_to(ROOT).as_posix()
    # Keep the binaries actually used in the complete-block checks. No need to
    # copy every compiler intermediate or the original 2.5 GB model.
    if folder.name.endswith("-cache") and folder.name.startswith(
        ("real-linear-conv-", "real-linear-qhost-")
    ):
        for name in ["compiler-dump/graph_1/aipu.bin", "context.onnx"]:
            path = folder / name
            if path.is_file():
                files[path] = path.relative_to(ROOT).as_posix()
    for path in folder.glob("profile*.json"):
        files[path] = path.relative_to(ROOT).as_posix()
for path in ROOT.glob("projection-*.npy"):
    files[path] = path.name
for path in Path("/tmp").glob("codex-qwen3*.log"):
    files[path] = "logs/" + path.name
for name in [
    "codex-qwen3-npu-preflight-final.json",
    "codex-qwen3-npu-existing-services.json",
]:
    path = Path("/tmp") / name
    if path.is_file():
        files[path] = name
frozen = subprocess.run(
    [str(ROOT / ".venv/bin/python"), "-m", "pip", "freeze"],
    text=True,
    capture_output=True,
    check=False,
)
# This venv intentionally has no pip module. Use the system pip targeting it.
if frozen.returncode:
    frozen = subprocess.run(
        ["python3", "-m", "pip", "--python", str(ROOT / ".venv/bin/python"), "freeze"],
        text=True,
        capture_output=True,
        check=True,
    )
manifest = [
    {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    for path, relative in sorted(files.items(), key=lambda pair: pair[1])
]
archive = Path("/tmp/codex-qwen3-npu-results.zip")
with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
    for path, relative in files.items():
        z.write(path, relative)
    z.writestr("artifact-manifest.json", json.dumps(manifest, indent=2))
    z.writestr("pip-freeze.txt", frozen.stdout)
print(
    json.dumps(
        {
            "path": str(archive),
            "bytes": archive.stat().st_size,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "files": len(files),
        }
    )
)
