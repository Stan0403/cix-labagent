"""Install only the unused NPU OCR adapter after verifying the current source."""
from pathlib import Path
import hashlib
import json
import shutil

work=Path.home()/'.local/state/codex-model-accel/20261117'
root=Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
target=root/'scripts/cix_ocr_service.py'
stage=work/'staged/cix_ocr_service.py'
plan=json.loads((work/'staged/ocr-fix-plan.json').read_text())
assert hashlib.sha256(target.read_bytes()).hexdigest()==plan['old_sha256'], 'Board source changed'
assert hashlib.sha256(stage.read_bytes()).hexdigest()==plan['new_sha256']
backup=work/'source-backup/cix_ocr_service.py'
assert not backup.exists(), 'Preserve original backup'
shutil.copy2(target,backup)
temporary=target.with_name('.codex-'+target.name)
temporary.write_bytes(stage.read_bytes())
temporary.replace(target)
(work/'ocr-source-install.json').write_text(json.dumps({'path':str(target),**plan,'production_route_changed':False},indent=2))
print('OCR source updated; production VLM route and service units unchanged.')
