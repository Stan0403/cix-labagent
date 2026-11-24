"""Exercise the actual web observation code and live adapter with an isolated DB."""

import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from labagent.config import Settings
from labagent.main import create_app

data = Path(tempfile.mkdtemp(prefix='codex-lcd-reader-workflow-'))
settings = Settings.load().model_copy(update={
    'data_dir': data, 'chat_provider': 'rules', 'llm_base_url': '',
})
assert settings.ocr_base_url == 'http://127.0.0.1:8031'
assert data.resolve() != (ROOT / 'data').resolve()
batch = ROOT / 'data/ocr-capture/20261122_191101_3fbaeb8d'
rows = [json.loads(line) for line in (batch / 'reviews/manual-review-user81.jsonl').read_text().splitlines()]
cases = [
    ('fast-positive', batch / rows[1]['path'], 1.25, ''),
    ('disagreement-rejected', batch / rows[64]['path'], None, ''),
    ('leading-decimal-fallback', Path('/tmp/codex-ocr-training-inventory/candidate-006.png'), .419, '0,0,1,1'),
]
records = []
with TestClient(create_app(settings)) as client:
    for name, photo, expected, roi in cases:
        response = client.post('/api/sessions', json={'title': 'Stored-photo regression, isolated DB', 'plan_id': 'battery-voltage-15v'})
        assert response.status_code == 201, response.text
        state = response.json()
        for kind, values in [
            ('confirm_step', {'confirmed': True}), ('advance', {}),
            ('confirm_equipment', {'objects': ['battery', 'multimeter', 'probes'], 'confirmed': True}), ('advance', {}),
            ('confirm_step', {'confirmed': True, 'meter_range': 'dc_20v'}), ('advance', {}),
            ('confirm_step', {'confirmed': True}), ('advance', {}),
        ]:
            response = client.post(f"/api/sessions/{state['id']}/actions", json={'kind': kind, **values})
            assert response.status_code == 200, response.text
            result = response.json()
            assert result['accepted'], result
            state = result['session']
        start = time.perf_counter()
        response = client.post(f"/api/sessions/{state['id']}/observe",
            files={'file': (photo.name, photo.read_bytes(), 'image/png' if photo.suffix == '.png' else 'image/jpeg')},
            data={'task': 'reading', 'automatic': 'false', 'roi': roi})
        seconds = time.perf_counter() - start
        assert response.status_code == 200, response.text
        result = response.json()
        candidate = result['candidate']
        assert candidate['value'] == expected, result
        assert not result['session']['measurements']
        assert candidate['reference'].startswith('image:') and candidate['roi']
        if name == 'fast-positive':
            assert candidate['backend'] == 'ppocr-lcd-v3-user81-onnxruntime-cpu'
        else:
            assert candidate['backend'] == 'qwen3-vl-4b-q4_k_m-llamacpp-cpu'
        records.append({'case': name, 'candidate': candidate, 'seconds': seconds, 'auto_roi': result['auto_roi']})
        print(name, candidate['value'], candidate['backend'], round(seconds, 3), flush=True)
report = {'passed': True, 'records': records, 'data_directory': str(data),
    'production_experiment_records_modified': False,
    'limits': 'Stored development photos, workflow prerequisites simulated only in isolated DB; actual HTTP model calls, no model mocks, no physical experiment validation'}
Path('/tmp/codex-lcd-reader-workflow-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({'passed': True, 'cases': len(records)}), flush=True)
