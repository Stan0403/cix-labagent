"""Download a read-only source snapshot from the authorized O6 board."""

import getpass
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import tarfile

import paramiko


BASE = "/home/radxa/referenced-chatgpt-conversation-this-is-an"
DEST = Path(__file__).resolve().parents[1]
EXCLUDED = {
    ".git", "__pycache__", "node_modules", "models", "data", ".runtime",
    "outputs", "work", "build", "dist", ".pytest_cache", ".cache",
    "test-results", ".tmp", ".ruff_cache",
}
SUFFIXES = {".py", ".js", ".cjs", ".html", ".css", ".json", ".md", ".toml", ".sh", ".ps1", ".cmd", ".txt"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-reference-only", action="store_true")
    args = parser.parse_args()
    password = getpass.getpass("O6 SSH password: ")
    client = paramiko.SSHClient()
    client.load_host_keys(str(Path(os.environ["TEMP"]) / "codex-o6-known_hosts"))
    client.connect("172.20.10.2", username="radxa", password=password,
                   look_for_keys=False, allow_agent=False, timeout=15)
    del password
    client.get_transport().set_keepalive(15)
    inventory = []
    if args.model_reference_only:
        with client.open_sftp() as sftp:
            for name in ("README.md", "ppocr-cix/pp_ocr.py", "sensevoice-cix/model/config.yaml",
                         "qwen25vl-mnn/config.json", "qwen25vl-mnn/llm_config.json"):
                remote = BASE + "/LabAgent/models/" + name
                target = DEST / "shared-model-reference" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(remote, str(target))
                content = target.read_bytes()
                inventory.append({"path": str(target.relative_to(DEST)), "remote": remote,
                                  "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        client.close()
        (DEST / "model-reference-inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Model reference:", len(inventory), "files", sum(item["bytes"] for item in inventory), "bytes")
        return
    remote_code = f'''
import os, sys, tarfile
from pathlib import Path
base = Path({BASE!r})
skip = {EXCLUDED!r}
suffixes = {SUFFIXES!r}
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
    for project in ("LabAgent-demo", "LabAgent"):
        for root, dirs, files in os.walk(base / project, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in skip and not d.startswith(".venv") and not d.endswith(".egg-info"))
            for name in sorted(files):
                source = Path(root) / name
                if source.is_symlink() or name == ".env":
                    continue
                if source.suffix in suffixes or name in {{".env.example", ".gitignore"}}:
                    archive.add(source, arcname=str(source.relative_to(base)), recursive=False)
'''
    _, stdout, stderr = client.exec_command("python3 -c " + shlex.quote(remote_code), timeout=60)
    with tarfile.open(fileobj=stdout, mode="r|gz") as archive:
        for item in archive:
            if not item.isfile():
                continue
            target = (DEST / item.name).resolve()
            if not target.is_relative_to(DEST) or item.name.split("/")[0] not in {"LabAgent-demo", "LabAgent"}:
                raise ValueError("Unexpected archive member")
            content = archive.extractfile(item).read()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            inventory.append({"project": item.name.split("/")[0], "path": str(target.relative_to(DEST)),
                              "remote": BASE + "/" + item.name, "bytes": len(content),
                              "mtime": item.mtime, "sha256": hashlib.sha256(content).hexdigest()})
    error = stderr.read().decode()
    if stdout.channel.recv_exit_status() != 0:
        raise RuntimeError(error)
    client.close()
    (DEST / "source-inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    for project in ("LabAgent-demo", "LabAgent"):
        items = [item for item in inventory if item["project"] == project]
        print(project, len(items), "files", sum(item["bytes"] for item in items), "bytes")


if __name__ == "__main__":
    main()
