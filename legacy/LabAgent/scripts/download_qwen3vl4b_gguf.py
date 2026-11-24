"""Download pinned official weights for the CIX-listed Qwen3-VL-4B."""
import hashlib
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "models" / "qwen3-vl-4b-gguf"
REVISION = "625828cbd3489786522366e19e22b6f12278e41a"
BASE = f"https://modelscope.cn/models/Qwen/Qwen3-VL-4B-Instruct-GGUF/resolve/{REVISION}/"
FILES = {
    "Qwen3VL-4B-Instruct-Q4_K_M.gguf": (2497281664, "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a"),
    "mmproj-Qwen3VL-4B-Instruct-F16.gguf": (836180256, "256f3a43bd4205ffef48d6b92715e1e70b5b0e9aef06522584967513a9985331"),
}


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(4 * 1024 * 1024):
            sha.update(block)
    return sha.hexdigest()


def download(name, size, expected):
    target = DEST / name
    if target.exists() and target.stat().st_size == size and digest(target) == expected:
        print(f"VERIFIED {name}", flush=True)
        return
    chunks = DEST / (name + ".chunks")
    chunks.mkdir(exist_ok=True)
    chunk_size = 8 * 1024 * 1024
    count = (size + chunk_size - 1) // chunk_size

    def fetch(index):
        start = index * chunk_size
        end = min(size, start + chunk_size) - 1
        path = chunks / f"{index:05d}"
        if path.exists() and path.stat().st_size == end - start + 1:
            return
        partial = path.with_suffix(".partial")
        headers = path.with_suffix(".headers")
        fragment = path.with_suffix(".fragment")
        # A stopped curl may have left a valid fragment not yet appended.
        have = partial.stat().st_size if partial.exists() else 0
        header = headers.read_text(errors="replace").lower() if headers.exists() else ""
        if (fragment.exists() and 0 < fragment.stat().st_size <= end - start + 1 - have
                and f"content-range: bytes {start + have}-{end}/{size}" in header):
            with partial.open("ab") as out, fragment.open("rb") as source:
                while block := source.read(1024 * 1024):
                    out.write(block)
            fragment.unlink()
        for attempt in range(30):
            have = partial.stat().st_size if partial.exists() else 0
            if have > end - start + 1:
                raise RuntimeError(f"Invalid saved chunk: {partial}")
            if have == end - start + 1:
                partial.replace(path)
                return
            result = subprocess.run([
                "curl", "-4", "-fsSL", "--connect-timeout", "20", "--max-time", "180",
                "-r", f"{start + have}-{end}", "-D", str(headers),
                "-o", str(fragment), BASE + name,
            ], capture_output=True, timeout=190)
            header = headers.read_text(errors="replace").lower() if headers.exists() else ""
            valid_range = f"content-range: bytes {start + have}-{end}/{size}" in header
            if valid_range and fragment.exists() and fragment.stat().st_size <= end - start + 1 - have:
                with partial.open("ab") as out, fragment.open("rb") as source:
                    while block := source.read(1024 * 1024):
                        out.write(block)
                fragment.unlink()
                if partial.stat().st_size == end - start + 1:
                    partial.replace(path)
                    return
            if attempt % 5 == 0:
                print(f"RETRY {name} chunk={index} attempt={attempt + 1} curl={result.returncode}", flush=True)
            time.sleep(min(attempt + 1, 10))
        raise RuntimeError(f"Download failed: {name} chunk {index}")

    print(f"DOWNLOAD {name} bytes={size} chunks={count}", flush=True)
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = [pool.submit(fetch, i) for i in range(count)]
        for completed, future in enumerate(as_completed(futures), 1):
            future.result()
            if completed % 8 == 0 or completed == count:
                print(f"PROGRESS {name} {completed}/{count}", flush=True)
    temporary = target.with_suffix(".gguf.assembling")
    with temporary.open("wb") as out:
        for index in range(count):
            with (chunks / f"{index:05d}").open("rb") as source:
                while block := source.read(4 * 1024 * 1024):
                    out.write(block)
    if temporary.stat().st_size != size or digest(temporary) != expected:
        raise RuntimeError(f"Checksum mismatch: {name}; weights will not be loaded")
    os.replace(temporary, target)
    print(f"VERIFIED {name} sha256={expected}", flush=True)


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    for name, (size, expected) in FILES.items():
        download(name, size, expected)
    print("ALL_WEIGHTS_VERIFIED", flush=True)


if __name__ == "__main__":
    main()
