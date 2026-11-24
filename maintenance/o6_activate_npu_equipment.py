"""Back up, install and activate the user-requested equipment-only NPU route."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

WORK=Path.home()/'.local/state/codex-model-accel/20261117/production-npu'
ROOT=Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
UNITS=Path.home()/'.config/systemd/user'
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))
NAMES=['labagent-demo-npu-vision.service','labagent-demo-equipment-vlm.service']
DROPIN=UNITS/'labagent-demo-vlm.service.d/equipment-npu.conf'
BACKUP=WORK/'backup'
ALLOWED={'scripts/qwen3vl_gguf_service.py','scripts/cix_npu_vision_worker.py','scripts/start_npu_equipment.sh',
         'labagent/demo_api.py','tests/test_equipment_npu.py',
         *('scripts/systemd/'+name for name in NAMES)}


def command(args,timeout=60):
    return subprocess.run(args,cwd=ROOT,capture_output=True,text=True,check=True,timeout=timeout)


def healthy(port,timeout=180):
    started=time.monotonic()
    while time.monotonic()-started<timeout:
        try:
            with OPENER.open(f'http://127.0.0.1:{port}/health',timeout=4) as response:
                value=json.load(response)
                if response.status==200 and value.get('ready',True): return value
        except Exception: pass
        time.sleep(.5)
    raise TimeoutError('Model not healthy: '+str(port))


def restore():
    previous=json.loads((BACKUP/'state.json').read_text())
    for unit in reversed(NAMES):
        subprocess.run(['systemctl','--user','stop',unit],capture_output=True,timeout=45)
        subprocess.run(['systemctl','--user','disable',unit],capture_output=True,timeout=30)
    for entry in previous['files']:
        target=Path(entry['target'])
        if entry['existed']:
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(BACKUP/entry['backup'],target)
        else: target.unlink(missing_ok=True)
    command(['systemctl','--user','daemon-reload'])
    command(['systemctl','--user','restart','labagent-demo-vlm.service','labagent-demo-web.service'])
    healthy(8031)


if __name__=='__main__':
    if '--rollback' in sys.argv:
        restore(); print('CPU equipment route restored.'); raise SystemExit(0)
    plan=json.loads((WORK/'staged/source-manifest.json').read_text())
    assert {item['path'] for item in plan}==ALLOWED
    installed=WORK/'source-installed.json'
    prior={item['path']:item['new_sha256'] for item in json.loads(installed.read_text())} if installed.exists() else {}
    for item in plan:
        target=ROOT/item['path']; stage=WORK/'staged'/item['stage']
        assert target.resolve().is_relative_to(ROOT)
        current=hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        expected={item['old_sha256'],item['new_sha256']}
        if item['path'] in prior: expected.add(prior[item['path']])
        assert current in expected, 'Board source changed: '+item['path']
        assert hashlib.sha256(stage.read_bytes()).hexdigest()==item['new_sha256']
    for unit in NAMES:
        assert not (UNITS/unit).exists() or (BACKUP/'state.json').exists(), 'Unexpected existing unit'
    if not (BACKUP/'state.json').exists():
        entries=[]
        paths=[ROOT/item['path'] for item in plan]+[UNITS/name for name in NAMES]+[DROPIN]
        for i,path in enumerate(paths):
            relative=str(i)+'-'+path.name
            entries.append({'target':str(path),'existed':path.exists(),'backup':relative})
            if path.exists():
                BACKUP.mkdir(parents=True,exist_ok=True); shutil.copy2(path,BACKUP/relative)
        BACKUP.mkdir(parents=True,exist_ok=True)
        (BACKUP/'state.json').write_text(json.dumps({'files':entries},indent=2))
    result={'status':'installing','authorization':'User explicitly requested activation of the tested partial-NPU equipment route',
            'known_limitation':'Fixed 320 image grid and documented numerical deviation; CPU reading and step checks preserved'}
    try:
        model_source=WORK.parent/'npu-vision-candidate/vision.cix'
        digest=hashlib.sha256(model_source.read_bytes()).hexdigest()
        assert digest=='fd315596a7028cbc649be09576fbe885ec165ae92881df394fd0036869d7e4d0'
        model_dir=ROOT/'models/qwen3-vl-4b-npu'; model_dir.mkdir(exist_ok=True)
        model=model_dir/'vision.cix'
        if not model.exists(): os.link(model_source,model)
        else: assert hashlib.sha256(model.read_bytes()).hexdigest()==digest
        shutil.copy2(WORK.parent/'npu-vision-candidate/LICENSE.txt',model_dir/'LICENSE.txt')
        (model_dir/'deployment-manifest.json').write_text(json.dumps({'sha256':digest,'revision':'f0ee7fef80bd137483da2d988a1c4a2813066036',
            'source':'cix/ai_model_hub_26_Q2/models/MultiModal/Qwen3-VL-4B-Instruct/vision.cix','execution':'NPU vision encoder only'},indent=2))
        for item in plan:
            target=ROOT/item['path']; target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name('.codex-npu-'+target.name)
            temporary.write_bytes((WORK/'staged'/item['stage']).read_bytes())
            os.chmod(temporary,item['mode']); temporary.replace(target)
        installed.write_text(json.dumps(plan,indent=2))
        command(['bash','-n',str(ROOT/'scripts/start_npu_equipment.sh')])
        checks=command([str(ROOT/'.venv-app/bin/python'),'-m','pytest','tests/test_equipment_npu.py',
                        'tests/test_document_workflow.py','tests/test_visual_workflow.py','-q'],timeout=180)
        (WORK/'unit-tests.log').write_text(checks.stdout+'\n'+checks.stderr)
        for name in NAMES: shutil.copy2(ROOT/'scripts/systemd'/name,UNITS/name)
        command(['systemctl','--user','daemon-reload'])
        command(['systemctl','--user','enable',*NAMES])
        command(['systemctl','--user','start',*NAMES],timeout=90)
        result['equipment_model_health']=healthy(8034)
        result['status']='models_ready'
        (WORK/'deployment.json').write_text(json.dumps(result,indent=2))
        can_reuse=('--reuse-preflight' in sys.argv and (WORK/'preflight-results.json').exists()
                   and json.loads((WORK/'preflight-results.json').read_text()).get('passed') is True
                   and prior=={item['path']:item['new_sha256'] for item in plan})
        if can_reuse:
            result['preflight_reused_for_identical_sources']=True
        else:
            preflight=command([str(ROOT/'.venv-app/bin/python'),str(WORK/'verify_equipment.py'),'--preflight'],timeout=600)
            (WORK/'preflight.log').write_text(preflight.stdout+'\n'+preflight.stderr)
        DROPIN.parent.mkdir(exist_ok=True)
        DROPIN.write_text('[Unit]\nWants=labagent-demo-equipment-vlm.service\nAfter=labagent-demo-equipment-vlm.service\n'
                          '[Service]\nEnvironment=LABAGENT_EQUIPMENT_VLM_SERVER=http://127.0.0.1:8034\n')
        command(['systemctl','--user','daemon-reload'])
        command(['systemctl','--user','restart','labagent-demo-vlm.service'])
        result['adapter_health']=healthy(8031,60)
        assert result['adapter_health'].get('equipment_ready') is True
        command(['systemctl','--user','restart','labagent-demo-web.service'])
        verification=command([str(ROOT/'.venv-app/bin/python'),str(WORK/'verify_equipment.py'),'--formal'],timeout=600)
        (WORK/'formal-verification.log').write_text(verification.stdout+'\n'+verification.stderr)
        result['status']='active_and_verified'
    except Exception as error:
        result['error']=repr(error)
        if isinstance(error,subprocess.CalledProcessError):
            result['stdout']=error.stdout; result['stderr']=error.stderr
        restore(); result['status']='rolled_back'
    (WORK/'deployment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if result['status']!='active_and_verified': raise SystemExit(1)
