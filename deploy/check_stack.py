"""Check actual readiness; a running unit alone is not a healthy model."""
import argparse
import json
import time
import urllib.request

p = argparse.ArgumentParser()
p.add_argument('--wait', type=float, default=0)
a = p.parse_args()
deadline = time.monotonic() + a.wait
while True:
    errors, results = [], {}
    for port, route in [(8001, '/api/health'), (8081, '/health'), (8011, '/health'),
                        (8031, '/health'), (8032, '/health'), (8034, '/health'), (8035, '/health')]:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}{route}', timeout=3) as response:
                results[str(port)] = json.load(response)
        except Exception as exc:
            errors.append(f'{port}: {type(exc).__name__}')
    adapter = results.get('8031', {})
    if adapter.get('reading_ready') is not True:
        errors.append('8031: LCD reader is not ready')
    if not errors or time.monotonic() >= deadline:
        break
    time.sleep(2)
print(json.dumps({'healthy': not errors, 'errors': errors, 'services': results}, ensure_ascii=False, indent=2))
raise SystemExit(bool(errors))
