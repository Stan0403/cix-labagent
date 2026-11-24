"""Install the tested digit-disagreement fix without changing other routes."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
import zipfile

root = Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
relative = 'scripts/qwen3vl_gguf_service.py'
destination = root / relative
with zipfile.ZipFile('/tmp/codex-lcd-reader-deploy.zip') as archive:
    previous = archive.read(relative)
assert destination.read_bytes() == previous, 'Adapter changed since installation'
deployment_path = Path('/tmp/codex-lcd-reader-deployment.json')
deployment = json.loads(deployment_path.read_text())
state = Path(deployment['state_directory'])
backup = state / 'adapter-before-digit-guard.py'
shutil.copy2(destination, backup)
new_bytes = Path('/tmp/codex-lcd-adapter-guard.py').read_bytes()
compile(new_bytes, str(destination), 'exec')
temporary = destination.with_suffix('.codex-tmp')
temporary.write_bytes(new_bytes)
os.replace(temporary, destination)
try:
    subprocess.run(['systemctl', '--user', 'restart', 'labagent-demo-vlm.service'], check=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(40):
        try:
            with opener.open('http://127.0.0.1:8031/health', timeout=3) as response:
                health = json.load(response)
            if health.get('reading_ready') and health.get('ready'):
                break
        except (OSError, ValueError):
            pass
        time.sleep(1)
    else:
        raise RuntimeError('Updated adapter failed health check')
except Exception:
    shutil.copy2(backup, destination)
    subprocess.run(['systemctl', '--user', 'restart', 'labagent-demo-vlm.service'], check=True)
    raise
deployment['digit_disagreement_guard'] = {
    'installed': True,
    'adapter_sha256': hashlib.sha256(new_bytes).hexdigest(),
    'backup_before_guard': str(backup),
    'reason': 'formal route regression: VLM .24 versus OCR 1.24 with incomplete pixel glyph count',
}
deployment['adapter_health'] = health
serialized = json.dumps(deployment, ensure_ascii=False, indent=2)
deployment_path.write_text(serialized)
(state / 'deployment.json').write_text(serialized)
print(json.dumps({'updated': True, 'reading_ready': health['reading_ready']}), flush=True)
