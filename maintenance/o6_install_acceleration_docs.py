"""Install reviewed Markdown after checking the source snapshot and staging hashes."""
from pathlib import Path
import hashlib
import json
import shutil

WORK = Path.home()/'.local/state/codex-model-accel/20261117'
ROOT = Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
allowed = {'README.md', 'docs/MODEL_ACCELERATION.md', 'docs/LOCAL_LLM.md', 'docs/QWEN3_VL_4B_DEPLOYMENT.md', 'docs/LOCAL_OCR.md', 'docs/NPU_EQUIPMENT.md'}
plan = json.loads((WORK/'staged/docs-plan.json').read_text())
assert {item['path'] for item in plan} == allowed
for item in plan:
    target = ROOT/item['path']
    current = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
    assert current in (item['old_sha256'], item['new_sha256']), 'Document changed since snapshot: '+item['path']
    stage = WORK/'staged'/('doc-'+target.name)
    assert hashlib.sha256(stage.read_bytes()).hexdigest() == item['new_sha256']

backup = WORK/'docs-backup'
backup.mkdir(exist_ok=True)
for item in plan:
    target = ROOT/item['path']
    if target.exists():
        saved = backup/item['path']
        saved.parent.mkdir(parents=True, exist_ok=True)
        if not saved.exists(): shutil.copy2(target, saved)
    temporary = target.with_name('.codex-'+target.name)
    temporary.write_bytes((WORK/'staged'/('doc-'+target.name)).read_bytes())
    temporary.replace(target)
print(json.dumps({'documents_installed': len(plan)}))
