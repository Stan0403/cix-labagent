"""Apply the faster CPU runtime after measurements, then validate real tool behavior."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import time

WORK=Path.home()/'.local/state/codex-model-accel/20261117'
ROOT=Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
UNIT='labagent-demo-llm'


def rollback():
    dropin=Path.home()/'.config/systemd/user'/f'{UNIT}.service.d/model-acceleration.conf'
    backup=WORK/'systemd-backup'/UNIT/'model-acceleration.conf'
    if backup.exists(): shutil.copy2(backup,dropin)
    else: dropin.unlink(missing_ok=True)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','restart',UNIT],check=True)

while subprocess.run(['systemctl','--user','is-active','--quiet','codex-remaining-probes-20261117'],
                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:
    time.sleep(2)

result={'status':'starting'}
with (WORK/'promotion.log').open('w') as log:
    code=subprocess.run([sys.executable,str(WORK/'deploy.py'),'--component','llm','--backend','cpu'],
                        stdout=log,stderr=subprocess.STDOUT,timeout=600).returncode
    if code:
        rollback()
        result={'status':'deployment_failed','exit':code}
    else:
        command=[sys.executable,str(ROOT/'scripts/check_local_llm.py'),'--base','http://127.0.0.1:8081/v1',
                 '--output',str(WORK/'llm-integration.json')]
        try:
            code=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=900).returncode
        except subprocess.TimeoutExpired:
            code=124
        if code==0:
            result={'status':'promoted_and_integration_passed','integration_exit':0}
        else:
            rollback()
            result={'status':'integration_failed_rolled_back','integration_exit':code}
(WORK/'promotion-result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
