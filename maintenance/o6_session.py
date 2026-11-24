"""Pinned SSH session for the user's O6: source sync, diagnostics, and file transfer.

Passwords are requested interactively and kept only in process memory.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import sys
import tarfile
import time
import warnings
import zipfile

warnings.filterwarnings("ignore", category=Warning, module="paramiko.*")
import paramiko

BASE = "/home/radxa/referenced-chatgpt-conversation-this-is-an"
ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {"LabAgent-demo", "LabAgent"}
SKIP = {".git", "__pycache__", "node_modules", "models", "data", ".runtime", "outputs",
        "work", "build", "dist", ".pytest_cache", ".cache", "test-results", ".tmp", ".ruff_cache"}
SUFFIXES = {".py", ".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".css",
            ".json", ".md", ".toml", ".sh", ".ps1", ".cmd", ".txt", ".yaml", ".yml",
            ".ini", ".cfg", ".c", ".cpp", ".h", ".hpp", ".service", ".svg", ".lock"}
SPECIAL = {".env.example", ".gitignore", "LICENSE", "Makefile", "Dockerfile"}


class PinnedHost(paramiko.MissingHostKeyPolicy):
    def __init__(self, keys, alias):
        self.pins = keys.lookup(alias)
        if not self.pins:
            raise RuntimeError(f"No existing trusted host key for {alias}")

    def missing_host_key(self, client, hostname, key):
        if self.pins.get(key.get_name()) != key:
            raise paramiko.SSHException("O6 host key does not match previously trusted key")
        client.get_host_keys().add(hostname, key.get_name(), key)


def execute(client, command, password, sudo=False, timeout=120):
    if sudo:
        command = "sudo -S -p '' -- sh -c " + shlex.quote(command)
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if sudo:
        stdin.write(password + "\n")
        stdin.flush()
    stdin.channel.shutdown_write()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return {"exit": stdout.channel.recv_exit_status(), "out": out, "err": err}


def sync_sources(client, label):
    stamp = label + "-" + time.strftime("%H%M%S")
    snapshot = ROOT / "snapshots" / stamp
    snapshot.mkdir(parents=True, exist_ok=False)
    remote_code = f'''
import os, sys, io, json, hashlib, tarfile
from pathlib import Path
base=Path({BASE!r}); skip={SKIP!r}; suffixes={SUFFIXES!r}; special={SPECIAL!r}
items=[]
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
    for project in sorted({PROJECTS!r}):
        for root,dirs,files in os.walk(base/project,followlinks=False):
            dirs[:]=sorted(d for d in dirs if d not in skip and not d.startswith('.venv') and not d.endswith('.egg-info'))
            for name in sorted(files):
                path=Path(root)/name
                if path.is_symlink() or (name.startswith('.env') and name not in special): continue
                if path.suffix not in suffixes and name not in special: continue
                data=path.read_bytes()
                if len(data)>16_000_000: raise RuntimeError('Unexpected oversized source: '+str(path))
                relative=path.relative_to(base).as_posix(); stat=path.stat()
                info=tarfile.TarInfo(relative); info.size=len(data); info.mtime=stat.st_mtime; info.mode=stat.st_mode&0o777
                archive.addfile(info,io.BytesIO(data))
                items.append(dict(project=project,path=relative,remote=str(path),bytes=len(data),mtime=stat.st_mtime,sha256=hashlib.sha256(data).hexdigest()))
    data=json.dumps(items,ensure_ascii=False).encode()
    info=tarfile.TarInfo('snapshot-manifest.json'); info.size=len(data); archive.addfile(info,io.BytesIO(data))
'''
    _, stdout, stderr = client.exec_command("python3 -c " + shlex.quote(remote_code), timeout=90)
    archive_path = snapshot / "remote-sources.tar.gz"
    with archive_path.open("wb") as f:
        shutil.copyfileobj(stdout, f)
    error = stderr.read().decode(errors="replace")
    if stdout.channel.recv_exit_status():
        raise RuntimeError(error)
    data_by_path = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            p = PurePosixPath(member.name)
            if not member.isfile() or p.is_absolute() or ".." in p.parts:
                raise ValueError("Unsafe archive member")
            if member.name != "snapshot-manifest.json" and p.parts[0] not in PROJECTS:
                raise ValueError("Unexpected project in archive")
            data_by_path[member.name] = archive.extractfile(member).read()
    inventory = json.loads(data_by_path.pop("snapshot-manifest.json"))
    for item in inventory:
        content = data_by_path[item["path"]]
        if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("Source checksum mismatch: " + item["path"])
    if set(data_by_path) != {item["path"] for item in inventory}:
        raise ValueError("Archive and manifest disagree")
    old_manifest = ROOT / "source-inventory.json"
    previous = json.loads(old_manifest.read_text(encoding="utf-8")) if old_manifest.exists() else []
    old_by_path = {item["path"].replace("\\", "/"): item for item in previous}
    changed, added, removed, local_changes = [], [], [], []
    with zipfile.ZipFile(snapshot / "local-before-sync.zip", "w", zipfile.ZIP_DEFLATED) as backup:
        if old_manifest.exists(): backup.write(old_manifest, "source-inventory.json")
        for project in sorted(PROJECTS):
            for current_root, dirs, files in os.walk(ROOT/project):
                dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith('.venv') and not d.endswith('.egg-info')]
                for name in files:
                    file = Path(current_root)/name
                    if file.is_symlink() or (name.startswith('.env') and name not in SPECIAL): continue
                    if file.suffix in SUFFIXES or name in SPECIAL:
                        backup.write(file, file.relative_to(ROOT).as_posix())
        for relative, content in data_by_path.items():
            target = (ROOT / relative).resolve()
            if not target.is_relative_to(ROOT) or target.relative_to(ROOT).parts[0] not in PROJECTS:
                raise ValueError("Unexpected sync destination")
            if target.exists():
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                if relative in old_by_path and digest != old_by_path[relative]["sha256"]:
                    local_changes.append(relative)
                if digest != hashlib.sha256(content).hexdigest(): changed.append(relative)
            else: added.append(relative)
        for relative in old_by_path.keys() - data_by_path.keys():
            target = (ROOT / relative).resolve()
            if target.is_relative_to(ROOT) and target.relative_to(ROOT).parts[0] in PROJECTS and target.is_file():
                removed.append(relative)
    # All previous source files are backed up before the first source mutation.
    for relative, content in data_by_path.items():
        target = ROOT/relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
    for relative in removed:
        (ROOT/relative).unlink()
    old_manifest.write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding="utf-8")
    for item in inventory:
        if hashlib.sha256((ROOT/item["path"]).read_bytes()).hexdigest()!=item["sha256"]:
            raise ValueError("Final local verification failed")
    summary = dict(snapshot=str(snapshot),files=len(inventory),changed=changed,added=added,
                   removed=removed,local_changes_backed_up=local_changes,all_checksums_verified=True)
    (snapshot/"sync-summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',default='192.168.123.2')
    parser.add_argument('--known-hosts',default=str(Path(os.environ['TEMP'])/'codex-o6-known_hosts'))
    parser.add_argument('--host-key-alias',default='172.20.10.2')
    args=parser.parse_args()
    password=getpass.getpass('O6 SSH password: ')
    def connect():
        connection=paramiko.SSHClient(); connection.load_host_keys(args.known_hosts)
        connection.set_missing_host_key_policy(PinnedHost(connection.get_host_keys(),args.host_key_alias))
        connection.connect(args.host,username='radxa',password=password,look_for_keys=False,allow_agent=False,timeout=15)
        connection.get_transport().set_keepalive(15)
        return connection
    client=connect()
    print('O6_SESSION_READY',flush=True)
    try:
        for line in sys.stdin:
            if not line.strip(): continue
            try:
                request=json.loads(line); op=request['op']
                if op=='exit': break
                transport=client.get_transport()
                if transport is None or not transport.is_active():
                    client.close()
                    client=connect()
                    print('O6_SESSION_RECONNECTED',flush=True)
                if op=='run':
                    result=execute(client,request['command'],password,request.get('sudo',False),request.get('timeout',120))
                elif op=='sync': result=sync_sources(client,request.get('label','2026-11-17'))
                elif op in {'get','put'}:
                    local=Path(request['local']).resolve()
                    if not local.is_relative_to(ROOT): raise ValueError('Transfer destination/source outside workspace')
                    with client.open_sftp() as sftp:
                        if op=='get': local.parent.mkdir(parents=True,exist_ok=True); sftp.get(request['remote'],str(local))
                        else: sftp.put(str(local),request['remote'])
                    result={'transferred':str(local)}
                else: raise ValueError('Unknown operation')
                print(json.dumps({'ok':True,'result':result},ensure_ascii=False),flush=True)
            except Exception as exc:
                print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False),flush=True)
    finally:
        client.close()


if __name__=='__main__': main()
