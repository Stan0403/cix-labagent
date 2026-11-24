"""Deploy only changed files against the retrieved O6 baseline; reject concurrent edits."""
import base64
import hashlib
import io
import json
import shlex
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


root = Path(__file__).resolve().parent.parent
baseline = root.parent / "tmp/o6_workflow_base_20261030"
tracking = root / "workflow-deploy-manifest.json"
previous = json.loads(tracking.read_text()) if tracking.exists() else {}
allowed = {"labagent", "scripts", "configs", "knowledge", "fixtures", "tests", "docs"}
changed, expected, desired = [], {}, {}
buffer = io.BytesIO()
with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if rel.parts[0] not in allowed and rel.as_posix() not in {"README.md", ".env.example", "pyproject.toml"}:
            continue
        if "__pycache__" in rel.parts or path.suffix == ".pyc":
            continue
        raw = path.read_bytes()
        original = baseline / rel
        if original.exists() and raw == original.read_bytes():
            continue
        name = rel.as_posix()
        expected[name] = previous.get(name, sha(original.read_bytes()) if original.exists() else None)
        desired[name] = sha(raw)
        archive.writestr(name, raw)
        changed.append(name)
remote_code = r'''
import base64, hashlib, io, json, shutil, sqlite3, sys, zipfile
from datetime import datetime, timezone
from pathlib import Path
root = Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
payload = json.load(sys.stdin)
z = zipfile.ZipFile(io.BytesIO(base64.b64decode(payload['archive'])))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
for name in z.namelist():
    assert not name.startswith('/') and '..' not in Path(name).parts
    current = sha(root/name)
    if current not in {payload['expected'][name], payload['desired'][name]}:
        raise RuntimeError('O6 文件自基线后发生其他修改，已停止部署：'+name)
backup = root.parent/'LabAgent-backups'/('workflow-deploy-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir(parents=True)
for p in (root/'data').glob('*.sqlite3'):
    with sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True) as src, sqlite3.connect(backup/p.name) as dst:
        src.backup(dst)
    (backup/p.name).chmod(0o600)
for name in z.namelist():
    target = root/name
    if target.exists():
        dest=backup/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,dest)
z.extractall(root)
(backup/'deployment.json').write_text(json.dumps({'expected':payload['expected'],'desired':payload['desired']},indent=2))
print(json.dumps({'deployed':len(z.namelist()),'backup':str(backup),'files':z.namelist()}))
'''
args = ["ssh", "-T", "-i", str(Path.home()/".ssh/o6_labagent"), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "radxa@172.20.10.2", "python3 -c " + shlex.quote(remote_code)]
payload = {"archive": base64.b64encode(buffer.getvalue()).decode(), "expected": expected, "desired": desired}
result = subprocess.run(args, input=json.dumps(payload).encode(), capture_output=True, check=True)
tracking.write_text(json.dumps({**previous, **desired}, indent=2))
print(result.stdout.decode())
