"""Read-only deployment verification, with no secrets or experiment/session writes."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import urllib.request

WORK = Path.home() / '.local/state/codex-model-accel/20261117'
ROOT = Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
PRODUCTION = WORK/'production-npu'
deployment = json.loads((PRODUCTION/'deployment.json').read_text()) if (PRODUCTION/'deployment.json').exists() else {}
equipment_active = deployment.get('status') == 'active_and_verified'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
UNITS = ['labagent-demo-web', 'labagent-demo-asr', 'labagent-demo-vlm',
         'labagent-qwen3vl4b', 'labagent-demo-llm']
if equipment_active:
    UNITS += ['labagent-demo-npu-vision', 'labagent-demo-equipment-vlm']


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    return {'exit': result.returncode, 'out': result.stdout.strip(), 'err': result.stderr.strip()}


result = {'units': {}, 'health': {}, 'sources': [], 'temporary_listeners': []}
for unit in UNITS:
    value = run(['systemctl', '--user', 'show', unit, '--property=MainPID,ActiveState,SubState'])
    props = dict(line.split('=', 1) for line in value['out'].splitlines() if '=' in line)
    entry = {'properties': props}
    pid = int(props.get('MainPID', '0'))
    if pid:
        proc = Path('/proc') / str(pid)
        entry['command'] = proc.joinpath('cmdline').read_bytes().decode().split('\0')[:-1]
        entry['cpu_affinity'] = sorted(os.sched_getaffinity(pid))
        # Whitelist backend settings only; never copy entire process environments.
        allowed = {'LABAGENT_ACCEL_BACKEND', 'VK_ICD_FILENAMES', 'MTMD_BACKEND_DEVICE', 'LABAGENT_VLM_BACKEND',
                   'LABAGENT_EQUIPMENT_VLM_SERVER', 'LABAGENT_NPU_VISION_SOCKET'}
        entry['backend_environment'] = {k: v for chunk in proc.joinpath('environ').read_bytes().split(b'\0')
                                         if b'=' in chunk for k, v in [chunk.decode().split('=', 1)] if k in allowed}
        handles = []
        for fd in proc.joinpath('fd').iterdir():
            try:
                target = os.readlink(fd)
                if target.startswith('/dev/aipu'): handles.append(target)
            except OSError:
                pass
        entry['npu_device_handles'] = handles
    result['units'][unit] = entry

for port in [8001, 8011, 8031, 8032, 8081] + ([8034] if equipment_active else []):
    try:
        path = '/api/health' if port == 8001 else '/health'
        with OPENER.open(f'http://127.0.0.1:{port}{path}', timeout=5) as response:
            result['health'][str(port)] = {'http': response.status, 'body': json.load(response)}
    except Exception as error:
        result['health'][str(port)] = {'error': repr(error)}

plans = {item['path']: item for item in json.loads((WORK/'staged/deploy-plan.json').read_text())}
if equipment_active:
    plans.update({item['path']: item for item in json.loads((PRODUCTION/'source-installed.json').read_text())})
for item in plans.values():
    path = ROOT/item['path']
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result['sources'].append({'path': item['path'], 'sha256': digest,
                              'matches_staged': digest == item['new_sha256']})

result['llm_dropin'] = (Path.home()/'.config/systemd/user/labagent-demo-llm.service.d/model-acceleration.conf').read_text()
result['promotion'] = json.loads((WORK/'promotion-result.json').read_text())
if equipment_active:
    result['equipment_deployment'] = deployment['status']
    result['equipment_formal_check'] = json.loads((PRODUCTION/'formal-results.json').read_text())['passed']
    result['equipment_worker'] = json.loads((Path(os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))/'labagent-npu-vision.json').read_text())
result['memory'] = run(['free', '-h'])
listeners = run(['ss', '-ltnp'])
result['temporary_listeners'] = [line for line in listeners['out'].splitlines()
                                 if any(f':{port} ' in line for port in [18021, 18032, 18034, 18042, 18043, 18081, 18082])]
result['passed'] = (
    all(item['properties'].get('ActiveState') == 'active' for item in result['units'].values())
    and all(item.get('http') == 200 and item.get('body',{}).get('ready',True) for item in result['health'].values())
    and all(item['matches_staged'] for item in result['sources'])
    and result['promotion']['status'] == 'promoted_and_integration_passed'
    and bool(result['units']['labagent-demo-asr']['npu_device_handles'])
    and not result['temporary_listeners']
)
if equipment_active:
    result['passed'] = result['passed'] and result['equipment_formal_check'] and bool(result['units']['labagent-demo-npu-vision']['npu_device_handles'])
(WORK/'final-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps({'passed': result['passed'], 'health': {k: v.get('http') for k, v in result['health'].items()},
                  'temporary_listeners': result['temporary_listeners']}, ensure_ascii=False))
raise SystemExit(0 if result['passed'] else 1)
