"""Read-only runtime inventory and direct, stored-image inference timing."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
from dotenv import dotenv_values
from PIL import Image

ROOT = Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
OUT = Path('/tmp/codex-o6-review-20261121')
OUT.mkdir(exist_ok=True)
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    return result.stdout.strip()

def inventory():
    result = {'at': command(['date', '-Is']), 'units': {}, 'health': {}, 'config': {}, 'reports': {}}
    names = ['labagent-demo-web', 'labagent-demo-vlm', 'labagent-demo-yolo',
             'labagent-demo-llm', 'labagent-demo-asr', 'labagent-qwen3vl4b',
             'labagent-demo-equipment-vlm', 'labagent-demo-npu-vision']
    allowed = {'LABAGENT_PORT','LABAGENT_HOST','LABAGENT_CHAT_PROVIDER','LABAGENT_LLM_BASE_URL',
               'LABAGENT_LLM_MODEL','LABAGENT_OCR_PROVIDER','LABAGENT_OCR_BASE_URL',
               'LABAGENT_ASR_BASE_URL','LABAGENT_ASR_MODEL','LABAGENT_HTTP_TIMEOUT',
               'LABAGENT_VISION_PROVIDER','LABAGENT_AGENT_DECISION_FORMAT',
               'LABAGENT_AGENT_MAX_ACTIONS','LABAGENT_AGENT_TICKET_SECONDS',
               'LABAGENT_AGENT_SEMANTIC_EQUIPMENT','LABAGENT_EVIDENCE_TTL_SECONDS',
               'LABAGENT_ACCEL_BACKEND','LABAGENT_INFERENCE_CPUS',
               'LABAGENT_EQUIPMENT_YOLO_SERVER','LABAGENT_EQUIPMENT_VLM_SERVER',
               'LABAGENT_VLM_SERVER','LABAGENT_VLM_BACKEND','LABAGENT_YOLO_MODEL',
               'LABAGENT_YOLO_BACKEND','LABAGENT_YOLO_SIZE','LABAGENT_YOLO_CONFIDENCE'}
    envfile = dotenv_values(ROOT/'.env')
    result['config'] = {k:v for k,v in envfile.items() if k in allowed}
    for name in names:
        info = command(['systemctl','--user','show',name,'-p','MainPID','-p','ActiveState','-p','ExecMainStartTimestamp'])
        item = dict(line.split('=',1) for line in info.splitlines() if '=' in line)
        pid = item.get('MainPID', '0')
        if pid != '0':
            try:
                env = dict(part.split('=',1) for part in Path('/proc',pid,'environ').read_text().split('\x00') if '=' in part)
                item['environment'] = {k:v for k,v in env.items() if k in allowed}
                item['command'] = Path('/proc',pid,'cmdline').read_text().replace('\x00',' ').strip()
            except OSError as exc:
                item['error'] = str(exc)
        result['units'][name] = item
    with httpx.Client(trust_env=False, timeout=4) as client:
        for port, path in [(8001,'/api/health'),(8031,'/health'),(8035,'/health'),(8081,'/health'),(8032,'/health'),(8011,'/health')]:
            try:
                response = client.get(f'http://127.0.0.1:{port}{path}')
                result['health'][str(port)] = {'status':response.status_code,'body':response.json()}
            except Exception as exc:
                result['health'][str(port)] = {'error':str(exc)}
    for relative in ['models/equipment-yolo26-v1/npu-validation.json','test-results/workflow-real-model-validation.json']:
        path = ROOT/relative
        if path.is_file() and path.stat().st_size < 2_000_000:
            result['reports'][relative] = json.loads(path.read_text())
    reports=[]
    for start in [ROOT/'test-results',ROOT/'.runtime',ROOT/'.tmp',ROOT.parent/'outputs',ROOT.parent/'work']:
        if not start.exists():
            continue
        for directory,dirs,files in os.walk(start):
            depth=len(Path(directory).relative_to(start).parts)
            dirs[:]=[d for d in dirs if not d.startswith('.venv') and d not in {'node_modules','data','images','isolated-data','.git','models','__pycache__'}] if depth<4 else []
            for name in files:
                if name.endswith('.json') and any(t in name for t in ['report','validation','integration','live','model']):
                    path=Path(directory)/name
                    reports.append({'path':str(path),'bytes':path.stat().st_size})
    result['report_inventory']=reports[:150]
    (OUT/'runtime.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print('INVENTORY_SAVED',len(reports),flush=True)

def benchmark():
    result={'at':command(['date','-Is']),'stored_images':True,'production_sessions_modified':False,'cases':[]}
    image_path=ROOT/'data/images/4017346abd93f9916a9f8b1e009be3fb0eb9f1beaffff8ca06340a21ab570218.jpg'
    image=Image.open(image_path).convert('RGB')
    image.thumbnail((1200,1200))
    buffer=io.BytesIO(); image.save(buffer,format='JPEG',quality=85)
    data=buffer.getvalue()
    labels={'multimeter':'数字万用表','battery':'1.5V干电池','red_probe':'红表笔','black_probe':'黑表笔'}
    def record(name,response,start):
        item={'case':name,'seconds':round(time.perf_counter()-start,4),'status':response.status_code,'result':response.json()}
        result['cases'].append(item)
        (OUT/'benchmark.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(name,item['seconds'],item['status'],flush=True)
    with httpx.Client(trust_env=False,timeout=240) as client:
        for i in range(5):
            start=time.perf_counter()
            response=client.post('http://127.0.0.1:8031/inspect',files={'file':('stored.jpg',data,'image/jpeg')},data={'labels':json.dumps(labels,ensure_ascii=False)})
            record('equipment_'+str(i+1),response,start)
        for rule in ('meter_setup','battery_contact','battery_cleanup'):
            start=time.perf_counter()
            response=client.post('http://127.0.0.1:8031/check_step',files={'file':('stored.jpg',data,'image/jpeg')},data={'rule':rule})
            record(rule,response,start)
        previous=ROOT.parent/'LabAgent/data/images/82b563446df13fa42a84c4e587c1b1379c84699f36f6011775881f9216d007e8.jpg'
        raw=previous.read_bytes()
        start=time.perf_counter()
        response=client.post('http://127.0.0.1:8031/locate_display',files={'file':('stored.jpg',raw,'image/jpeg')})
        record('locate_display',response,start)
        roi=response.json().get('roi') if response.is_success else None
        if roi:
            from labagent.perception import crop_roi
            cropped=crop_roi(Image.open(io.BytesIO(raw)).convert('RGB'),roi)
            buffer=io.BytesIO();cropped.save(buffer,format='PNG')
            start=time.perf_counter()
            response=client.post('http://127.0.0.1:8031/recognize',files={'file':('display.png',buffer.getvalue(),'image/png')})
            record('read_display',response,start)
    print('BENCHMARK_SAVED',flush=True)

if __name__=='__main__':
    inventory()
    if '--benchmark' in sys.argv:
        benchmark()
