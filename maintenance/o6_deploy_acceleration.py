"""Promote a measured backend with source checks, user-unit backups, and rollback."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.request

WORK=Path.home()/'.local/state/codex-model-accel/20261117'
ROOT=Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
UNITS=Path.home()/'.config/systemd/user'
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def command(args):
    return subprocess.run(args,capture_output=True,text=True,check=True,timeout=35)


def health(port,timeout=240):
    start=time.monotonic()
    while time.monotonic()-start<timeout:
        try:
            with OPENER.open(f'http://127.0.0.1:{port}/health',timeout=3) as response:
                value=json.load(response)
                if response.status==200 and value.get('ready',True): return value
        except Exception: pass
        time.sleep(.5)
    raise TimeoutError(f'Service {port} not ready')


def install_sources():
    plan=json.loads((WORK/'staged/deploy-plan.json').read_text())
    saved=WORK/'source-installed.json'
    installed={item['path']:item for item in json.loads(saved.read_text())} if saved.exists() else {}
    backup=WORK/'source-backup'; backup.mkdir(exist_ok=True)
    pending=[]
    for item in plan:
        target=(ROOT/item['path']).resolve()
        assert target.is_relative_to(ROOT) and target.parent==ROOT/'scripts'
        stage=WORK/'staged'/target.name
        assert hashlib.sha256(stage.read_bytes()).hexdigest()==item['new_sha256']
        if target.exists():
            current=hashlib.sha256(target.read_bytes()).hexdigest()
            if current==item['new_sha256']: continue
            expected=installed.get(item['path'],{}).get('new_sha256',item['old_sha256'])
            assert current==expected,str(target)+' changed outside the acceleration task'
            original=backup/target.name
            if original.exists(): original=backup/(target.name+'.'+current[:12])
            if not original.exists(): shutil.copy2(target,original)
        else:
            assert item['old_sha256'] is None and item['path'] not in installed
        pending.append(item)
    for item in pending:
        target=ROOT/item['path']; stage=WORK/'staged'/target.name
        temporary=target.with_name('.codex-accel-'+target.name)
        temporary.write_bytes(stage.read_bytes()); os.chmod(temporary,item['mode']); temporary.replace(target)
    command(['bash','-n',str(ROOT/'scripts/start_accelerated_model.sh')])
    saved.write_text(json.dumps(plan,indent=2))


def write_dropin(unit,content,changes):
    directory=UNITS/(unit+'.service.d'); directory.mkdir(parents=True,exist_ok=True)
    path=directory/'model-acceleration.conf'
    previous=path.read_bytes() if path.exists() else None
    changes.append((path,previous))
    backup=WORK/'systemd-backup'/unit
    first_backup=not backup.exists()
    backup.mkdir(parents=True,exist_ok=True)
    # Copy the user's original fragment/drop-ins only to the board's private audit directory.
    source=UNITS/(unit+'.service')
    if first_backup:
        if source.exists(): shutil.copy2(source,backup/source.name)
        for old in directory.glob('*.conf'): shutil.copy2(old,backup/old.name)
    temporary=path.with_suffix('.tmp'); temporary.write_text(content); temporary.replace(path)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--component',choices=['llm','vlm'],required=True)
    parser.add_argument('--backend',choices=['cpu','vulkan','vulkan_vision'],required=True)
    args=parser.parse_args()
    if args.component=='llm':
        assert args.backend=='cpu','GPU dialogue benchmark did not improve performance'
        checks=json.loads((WORK/'llm-benchmark.json').read_text())
        assert any(r['variant']=='llm_optimized_cpu' and r['passed'] for r in checks)
        model_unit='labagent-demo-llm'; port=8081
    else:
        names={'vulkan':'vlm_vulkan_all','vulkan_vision':'vlm_vulkan_vision'}
        assert args.backend in names
        checks=json.loads((WORK/'vlm-benchmark.json').read_text())
        assert any(r['variant']==names[args.backend] and r['passed'] for r in checks)
        model_unit='labagent-qwen3vl4b'; port=8032
    install_sources()
    changes=[]
    result={'component':args.component,'backend':args.backend,'status':'pending','rebooted':False}
    try:
        content=('[Service]\nExecStart=\nExecStart=/bin/bash '+str(ROOT/'scripts/start_accelerated_model.sh')+' '+args.component+'\n'
                 'Environment=LABAGENT_ACCEL_BACKEND='+args.backend+'\n'
                 'Environment=VK_ICD_FILENAMES=/etc/vulkan/icd.d/mali.json\n')
        write_dropin(model_unit,content,changes)
        if args.component=='vlm':
            label='qwen3-vl-4b-q4_k_m-llamacpp-'+('vulkan-vision-cpu-text' if args.backend=='vulkan_vision' else 'vulkan')
            write_dropin('labagent-demo-vlm','[Service]\nEnvironment=LABAGENT_VLM_BACKEND='+label+'\n',changes)
        command(['systemctl','--user','daemon-reload'])
        command(['systemctl','--user','restart',model_unit])
        result['health']=health(port)
        if args.component=='vlm':
            command(['systemctl','--user','restart','labagent-demo-vlm'])
            result['adapter_health']=health(8031,40)
            assert result['adapter_health']['backend']==label
        result['status']='promoted'
    except Exception as error:
        result['error']=repr(error)
        for path,previous in reversed(changes):
            if previous is None: path.unlink(missing_ok=True)
            else: path.write_bytes(previous)
        command(['systemctl','--user','daemon-reload'])
        command(['systemctl','--user','restart',model_unit])
        result['rollback_health']=health(port,120)
        if args.component=='vlm':
            command(['systemctl','--user','restart','labagent-demo-vlm']); health(8031,40)
        result['status']='rolled_back'
    path=WORK/('deployment-'+args.component+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if result['status']!='promoted': raise SystemExit(1)
