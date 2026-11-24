"""Persist successful checks and synchronize reader documentation and regression files."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

root = Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
deployment_path = Path('/tmp/codex-lcd-reader-deployment.json')
deployment = json.loads(deployment_path.read_text())
state = Path(deployment['state_directory'])
assert state.parent == Path.home() / '.local/state/codex-lcd-reader'
assert deployment['status'] == 'activated' and deployment['digit_disagreement_guard']['installed']
for source in ['formal-verification', 'workflow-verification']:
    report = Path('/tmp/codex-lcd-reader-' + source + '.json')
    assert json.loads(report.read_text())['passed']
    shutil.copy2(report, state / report.name)
shutil.copy2('/tmp/codex-lcd-reader-native/report.json', state / 'native-report.json')

with zipfile.ZipFile('/tmp/codex-lcd-reader-final-metadata.zip') as archive:
    expected = json.loads(archive.read('expected-source-sha.json'))
    for relative, digest in expected.items():
        assert relative in {'scripts/qwen3vl_gguf_service.py', 'labagent/lcd_reader.py', 'labagent/readings.py'}
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest, relative
    for name in archive.namelist():
        if name == 'expected-source-sha.json':
            continue
        assert name.startswith(('docs/LCD_READER.md', 'tests/test_lcd_reader.py',
            'tests/fixtures/lcd/', 'data/ocr-training/20261122-user81/'))
        destination = (root / name).resolve()
        assert destination.is_relative_to(root.resolve())
        if destination.exists():
            backup = state / 'metadata-before-update' / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(archive.read(name))
    assert all(hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest for relative, digest in expected.items())

services = ['labagent-demo-web', 'labagent-demo-vlm', 'labagent-qwen3vl4b',
    'labagent-demo-yolo', 'labagent-demo-asr', 'labagent-demo-llm']
statuses = {s: subprocess.check_output(['systemctl', '--user', 'is-active', s], text=True).strip() for s in services}
assert all(value == 'active' for value in statuses.values())
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:8031/health', timeout=10) as response:
    health = json.load(response)
assert health['ready'] and health['reading_ready'] and health['reading_model_sha256'] == deployment['model_sha256']
with opener.open('http://127.0.0.1:8001/api/health', timeout=10) as response:
    assert json.load(response)['status'] == 'ok'
deployment['verified'] = {'formal_adapter_checks': 14, 'isolated_web_workflow_checks': 3,
    'local_regression_checks': 89, 'production_source_sha256': expected, 'services': statuses}
deployment['documentation'] = str(root / 'docs/LCD_READER.md')
serialized = json.dumps(deployment, ensure_ascii=False, indent=2)
deployment_path.write_text(serialized)
(state / 'deployment.json').write_text(serialized)
print(json.dumps({'finalized': True, 'reading_backend': health['reading_backend'],
    'services': statuses, 'checks': deployment['verified'], 'backup': str(state)}, ensure_ascii=False), flush=True)
