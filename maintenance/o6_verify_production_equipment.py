"""Real model verification with isolated replay state, never production records."""
from pathlib import Path
import argparse
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx
from fastapi.testclient import TestClient
from PIL import Image

WORK=Path.home()/'.local/state/codex-model-accel/20261117/production-npu'
ROOT=Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
IMAGE=ROOT/'data/images/4017346abd93f9916a9f8b1e009be3fb0eb9f1beaffff8ca06340a21ab570218.jpg'
NPU_BACKEND='qwen3-vl-4b-cix-npu-vision-cpu-text-320'
LABELS={'battery':'1.5 V 干电池','multimeter':'数字万用表','probes':'红黑表笔'}
RUNTIME=Path(os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))
sys.path.insert(0,str(ROOT))


def png(image):
    output=io.BytesIO(); image.save(output,format='PNG'); return output.getvalue()


def validate_scene(body):
    assert body['backend']==NPU_BACKEND,body
    assert all(body['present'].get(key) is True for key in LABELS),body
    assert len(body['boxes'])>=4,body
    for box in body['boxes']:
        x,y,w,h=box['bbox']
        assert 0<=x<1 and 0<=y<1 and w>0 and h>0 and x+w<=1.000001 and y+h<=1.000001,box


def preflight():
    os.environ['LABAGENT_EQUIPMENT_VLM_SERVER']='http://127.0.0.1:8034'
    spec=importlib.util.spec_from_file_location('production_equipment_preflight',ROOT/'scripts/qwen3vl_gguf_service.py')
    adapter=importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
    original=Image.open(IMAGE).convert('RGB')
    cases=[('scene',IMAGE.read_bytes()),('blank_negative',png(Image.new('RGB',(896,504),'white'))),
           ('missing_black_probe_negative',png(original.crop((0,0,1080,original.height))))]
    result={'mode':'preflight','cases':[]}
    with TestClient(adapter.app) as client:
        assert client.get('/health').json()['equipment_ready'] is True
        for name,data in cases:
            started=time.monotonic()
            response=client.post('/inspect',files={'file':('scene.png',data,'image/png')},
                                 data={'labels':json.dumps(LABELS,ensure_ascii=False)})
            assert response.status_code==200,response.text
            body=response.json()
            if name=='scene': validate_scene(body)
            elif name=='blank_negative': assert not any(value is True for value in body['present'].values()) and not body['boxes'],body
            else: assert body['present'].get('probes') is not True,body
            result['cases'].append({'case':name,'seconds':time.monotonic()-started,'passed':True,'result':body})
            (WORK/'preflight-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        before=json.loads((RUNTIME/'labagent-npu-vision.json').read_text())['requests']
        response=client.post('/recognize',files={'file':('screen.png',png(original.crop((490,405,685,525))),'image/png')})
        assert response.status_code==200,response.text
        body=response.json()
        assert body['backend']!=NPU_BACKEND and 'cpu' in body['backend']
        text=body['text'].strip()
        if text.startswith('```'): text=text.split('\n',1)[1].rsplit('```',1)[0]
        assert json.loads(text)['reading'] is None,body
        after=json.loads((RUNTIME/'labagent-npu-vision.json').read_text())['requests']
        assert before==after,'Reading unexpectedly used equipment NPU'
        result['cpu_reading']={'passed':True,'result':body}
    result['passed']=True
    (WORK/'preflight-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'preflight_passed':True,'seconds':[item['seconds'] for item in result['cases']]}))


def formal():
    from labagent.config import Settings
    from labagent.main import create_app
    result={'mode':'formal','database':'isolated temporary replay database'}
    with httpx.Client(timeout=15,trust_env=False) as client:
        health=client.get('http://127.0.0.1:8031/health'); health.raise_for_status()
        result['adapter_health']=health.json()
        assert result['adapter_health']['equipment_ready'] is True
        assert result['adapter_health']['equipment_backend']==NPU_BACKEND
        for attempt in range(60):
            try:
                web=client.get('http://127.0.0.1:8001/api/health'); web.raise_for_status()
                break
            except httpx.HTTPError:
                if attempt==59: raise
                time.sleep(.5)
        result['web_health']=web.json()
    (ROOT/'.tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='npu-production-check-',dir=ROOT/'.tmp') as temporary:
        settings=Settings(data_dir=Path(temporary),local_only=True,chat_provider='rules',
                          ocr_provider='mnn',ocr_base_url='http://127.0.0.1:8031',http_timeout=240)
        app=create_app(settings)
        with TestClient(app) as client:
            response=client.post('/api/sessions',json={'title':'隔离 NPU 接入验收','mode':'replay','plan_id':'battery-voltage-15v'})
            assert response.status_code==201,response.text
            state=response.json(); session_id=state['id']
            for action in [{'kind':'confirm_step','confirmed':True},{'kind':'advance'}]:
                response=client.post(f'/api/sessions/{session_id}/actions',json=action)
                assert response.status_code==200 and response.json()['accepted'],response.text
            started=time.monotonic()
            response=client.post(f'/api/sessions/{session_id}/observe',
                                 files={'file':('scene.jpg',IMAGE.read_bytes(),'image/jpeg')},data={'task':'objects'})
            assert response.status_code==200,response.text
            body=response.json(); validate_scene(body)
            assert body['session']['steps'][body['session']['step']]['gate']=='equipment'
            assert not body['session']['measurements']
            with sqlite3.connect(Path(temporary)/'labagent.sqlite3') as database:
                payload=database.execute("SELECT payload FROM events WHERE kind='perception.objects' AND session_id=? ORDER BY sequence DESC LIMIT 1",(session_id,)).fetchone()
                assert json.loads(payload[0])['input']['backend']==NPU_BACKEND
            result['workflow_scene']={'seconds':time.monotonic()-started,'passed':True,'result':body}
    pid=int(subprocess.check_output(['systemctl','--user','show','labagent-demo-npu-vision.service','--property=MainPID','--value'],text=True))
    handles=[]
    for fd in Path(f'/proc/{pid}/fd').iterdir():
        try:
            value=os.readlink(fd)
            if value.startswith('/dev/aipu'): handles.append(value)
        except OSError: pass
    assert handles,'Formal encoder lacks NPU device handle'
    result['npu_device_handles']=handles
    result['worker_status']=json.loads((RUNTIME/'labagent-npu-vision.json').read_text())
    assert result['worker_status']['requests']>0 and result['worker_status']['errors']==0
    result['passed']=True
    (WORK/'formal-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'formal_passed':True,'workflow_seconds':result['workflow_scene']['seconds'],'device_handles':handles}))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--preflight',action='store_true')
    parser.add_argument('--formal',action='store_true')
    args=parser.parse_args()
    if args.preflight: preflight()
    elif args.formal: formal()
    else: raise SystemExit('Choose --preflight or --formal')
