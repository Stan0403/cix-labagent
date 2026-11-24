"""Download the MNN reference weights cited by the CIX Qwen2.5-VL guide."""
import hashlib
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "models" / "qwen25vl-mnn"
REVISION = "57e28fbba82bafcd552b3171c59a3d93c4deccf2"
BASE = "https://modelscope.cn/models/MNN/Qwen2.5-VL-3B-Instruct-MNN/resolve/" + REVISION + "/"
FILES = {
    "config.json": "8183da75c6c7bda69a3f637ec1254dc6b3b3e0b6bb3cb9f98b12f8a7b1298815",
    "embeddings_bf16.bin": "b84907567aa829c6f24cadbdeb68c3c44d25fc0a8be8e917fd603cb64f72810d",
    "llm.mnn": "860ac54071174f8d2961c3258224fe315dcf2bae78344005cdddee21e7d80aa3",
    "llm.mnn.json": "04ae802933b4ff8e58c17b1246e8b64abd3267fb16c7b6642028771e87f88fcd",
    "llm.mnn.weight": "7326f768b816b5af71ad3a995dc1e321e07f85db487394e1430e5f07fd9627c6",
    "llm_config.json": "00492942d0603aa638ec263341359f33feeb400e36ca96d11fe1dde1b9f79e77",
    "tokenizer.txt": "b86f1298a0d6a1b2f312946c2f674f883f1d134ccabc79c42dd4c6b5beadf37b",
    "visual.mnn": "1caaa38f5c8e0073f7bbf29e496ccfbeda1b32df332652a031100d0ea306dd7f",
    "visual.mnn.weight": "617b17ee3431529b4c7f75be2b8028e7ff21da32821e83c181139e8a8af25f81",
}
SIZES = {"embeddings_bf16.bin": 622329856, "llm.mnn.weight": 1737291202,
         "visual.mnn.weight": 369748896}


def download_parts(client, url, part, size):
    chunk_size = 8 * 1024 * 1024
    if not part.exists() or part.stat().st_size != size:
        with part.open("wb") as output:
            output.truncate(size)

    def is_complete(start, end):
        # Preserve completed ranges after a network interruption. The complete
        # file is still checked against its published SHA-256 before use.
        try:
            with part.open("rb") as source:
                data = os.lseek(source.fileno(), start, os.SEEK_DATA)
                hole = os.lseek(source.fileno(), start, os.SEEK_HOLE)
            return data == start and hole >= end + 1
        except (OSError, AttributeError):
            return False

    def fetch(start):
        end = min(size, start + chunk_size) - 1
        if is_complete(start, end):
            return
        last_error = None
        chunk = part.with_name(part.name + f".{start}")
        headers = part.with_name(part.name + f".{start}.headers")
        fragment = part.with_name(part.name + f".{start}.fragment")
        for _ in range(20):
            try:
                have = chunk.stat().st_size if chunk.exists() else 0
                if have > end - start + 1:
                    raise RuntimeError("invalid partial range")
                if have < end - start + 1:
                    begin = start + have
                    headers.unlink(missing_ok=True)
                    fragment.unlink(missing_ok=True)
                    subprocess.run(["curl", "-4", "-fsSL", "--max-time", "300", "-r", f"{begin}-{end}",
                                    "-D", str(headers), "-o", str(fragment), url], check=False, timeout=310)
                    header_text = headers.read_text(errors="replace").lower()
                    if f"content-range: bytes {begin}-{end}/{size}" not in header_text:
                        raise RuntimeError("server did not honor the requested range")
                    if not fragment.exists() or fragment.stat().st_size > end - begin + 1:
                        raise RuntimeError("invalid partial data")
                    with chunk.open("ab") as output, fragment.open("rb") as source:
                        while block := source.read(1024 * 1024):
                            output.write(block)
                    fragment.unlink()
                    if chunk.stat().st_size != end - start + 1:
                        raise RuntimeError("incomplete range; continuing")
                with part.open("r+b") as output:
                    output.seek(start)
                    with chunk.open("rb") as source:
                        while block := source.read(1024 * 1024):
                            output.write(block)
                chunk.unlink()
                headers.unlink()
                return
            except (subprocess.SubprocessError, OSError, RuntimeError) as exc:
                last_error = exc
        raise RuntimeError(f"range download failed: {start}-{end}") from last_error

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, range(0, size, chunk_size)))


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1024 * 1024):
            sha.update(block)
    return sha.hexdigest()


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, connect=20), trust_env=False) as client:
        for name, expected in FILES.items():
            path = DEST / name
            if path.is_file() and digest(path) == expected:
                print(f"verified {name}", flush=True)
                continue
            part = DEST / (name + ".part")
            print(f"downloading {name}", flush=True)
            if name in SIZES:
                download_parts(client, BASE + name, part, SIZES[name])
            else:
                with client.stream("GET", BASE + name) as response:
                    response.raise_for_status()
                    with part.open("wb") as out:
                        for block in response.iter_bytes(1024 * 1024):
                            out.write(block)
            if digest(part) != expected:
                raise RuntimeError(f"SHA256 mismatch: {name}")
            os.replace(part, path)
            print(f"verified {name}", flush=True)


if __name__ == "__main__":
    main()
