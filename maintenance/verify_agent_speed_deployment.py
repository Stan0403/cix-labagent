"""Read-only verification of deployed code, health, services and decision defaults."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

APP=Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
WORK=Path('/tmp/codex-agent-speed')
sys.path.insert(0,str(APP))
from labagent.config import Settings
from labagent.providers import Gateway

settings=Settings.load()
manifest=json.loads((WORK/'candidate/optimization-manifest.json').read_text())
report={'source_hashes_match':all(hashlib.sha256((APP/e['path']).read_bytes()).hexdigest()==e['sha256']
                                for e in manifest['files']),
        'agent_max_tokens':settings.agent_max_tokens,'health':{},'services':{}}
import inspect
report['decision_adapter_loaded']='decision' in inspect.signature(Gateway.chat).parameters
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for port,path in [(8001,'/api/health'),(8081,'/health'),(8031,'/health'),(8035,'/health'),(8032,'/health'),(8011,'/health')]:
    headers={'Authorization':'Bearer '+settings.access_token} if port==8001 and settings.access_token else {}
    request=urllib.request.Request(f'http://127.0.0.1:{port}{path}',headers=headers)
    try:
        with opener.open(request,timeout=4) as response:
            report['health'][str(port)]={'status':response.status,'body':json.load(response)}
    except Exception as exc:
        report['health'][str(port)]={'error':str(exc)}
for name in ['labagent-demo-web','labagent-demo-llm','labagent-demo-vlm','labagent-demo-yolo','labagent-qwen3vl4b','labagent-demo-asr']:
    result=subprocess.run(['systemctl','--user','show',name,'-p','ActiveState','-p','MainPID'],capture_output=True,text=True,check=True)
    report['services'][name]=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
report['passed']=(report['source_hashes_match'] and report['decision_adapter_loaded']
                  and all(h.get('status')==200 for h in report['health'].values())
                  and all(u.get('ActiveState')=='active' for u in report['services'].values()))
(WORK/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False),flush=True)
if not report['passed']:
    raise SystemExit('Post-deployment verification failed')
