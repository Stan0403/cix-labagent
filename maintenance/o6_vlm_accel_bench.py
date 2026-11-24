"""Exercise the actual VLM adapter on fixed lab photographs, with no session writes."""
from pathlib import Path
import argparse
import importlib.util
import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

from PIL import Image
from starlette.datastructures import UploadFile

WORK = Path.home()/'.local/state/codex-model-accel/20261117'
ROOT = Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
BINARY = Path.home()/'llama.cpp/build-codex-vulkan/bin/llama-server'
MODELS = ROOT/'models/qwen3-vl-4b-gguf'
IMAGE = ROOT/'data/images/4017346abd93f9916a9f8b1e009be3fb0eb9f1beaffff8ca06340a21ab570218.jpg'
PORT = 18032
sys.path.insert(0,str(ROOT))
from o6_llm_accel_bench import wait_ready

spec=importlib.util.spec_from_file_location('accel_adapter',ROOT/'scripts/qwen3vl_gguf_service.py')
adapter=importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
LABELS={'battery':'单节干电池','multimeter':'数字万用表','probes':'红黑表笔'}


def frame_bytes(image):
    output=io.BytesIO(); image.save(output,format='JPEG',quality=95); return output.getvalue()


def read_json(text):
    value=text.strip()
    if value.startswith('```'):
        value=value.split('\n',1)[-1].rsplit('```',1)[0].strip()
    return json.loads(value)


def run_variant(name,base,flags=None,baseline_scene=None):
    process=None; log=None
    record={'variant':name,'cases':[]}
    try:
        if flags is not None:
            port=urlsplit(base).port
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                probe.bind(('127.0.0.1',port))
            env=os.environ.copy(); env['VK_ICD_FILENAMES']='/etc/vulkan/icd.d/mali.json'
            if name=='vlm_vulkan_vision': env['MTMD_BACKEND_DEVICE']='Vulkan0'
            command=['taskset','-c','0,1,6,7,8,9,10,11',str(BINARY),
                     '--model',str(MODELS/'Qwen3VL-4B-Instruct-Q4_K_M.gguf'),
                     '--mmproj',str(MODELS/'mmproj-Qwen3VL-4B-Instruct-F16.gguf'),
                     '--alias','qwen3-vl-4b-local','--host','127.0.0.1','--port',str(port),
                     '--ctx-size','4096','--threads','8','--threads-batch','8',
                     '--parallel','1','--jinja','--temp','0','--n-predict','512',*flags]
            path=WORK/(name+'.log')
            if path.exists():
                path.rename(WORK/(name+'-previous-'+str(int(time.time()))+'.log'))
            log=path.open('w')
            process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
            record['command']=command
            record['startup_seconds']=wait_ready(base,process)
        adapter.SERVER=base; adapter.BACKEND=name
        with Image.open(IMAGE) as source:
            original=source.convert('RGB')
        blank=frame_bytes(Image.new('RGB',(896,504),'white'))
        missing=frame_bytes(original.crop((0,0,1080,original.height)))
        screen=frame_bytes(original.crop((490,405,685,525)))
        cases=[('scene',IMAGE.read_bytes(),'inspect'),('blank_negative',blank,'inspect'),
               ('unlit_display_negative',screen,'recognize'),('missing_black_probe_negative',missing,'inspect')]
        for case,data,endpoint in cases:
            start=time.monotonic()
            try:
                file=UploadFile(file=io.BytesIO(data),filename='scene.jpg')
                if endpoint=='inspect': result=adapter.inspect(file,labels=json.dumps(LABELS,ensure_ascii=False))
                else: result=adapter.recognize(file)
                if case=='scene': passed=all(result['present'].get(k) is True for k in LABELS) and len(result['boxes'])>=4
                elif case=='blank_negative': passed=not any(v is True for v in result['present'].values()) and not result['boxes']
                elif case=='unlit_display_negative': passed=read_json(result['text']).get('reading') is None
                else: passed=result['present'].get('probes') is not True
                item={'case':case,'seconds':time.monotonic()-start,'passed':passed,'result':result}
            except Exception as error:
                item={'case':case,'seconds':time.monotonic()-start,'passed':False,'error':repr(error)}
            record['cases'].append(item)
            (WORK/'vlm-benchmark-progress.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
            print(json.dumps({'variant':name,'case':case,'seconds':round(item['seconds'],3),'passed':item['passed']}),flush=True)
            if flags is not None and case=='scene' and (item.get('error') or (baseline_scene and item['seconds']>baseline_scene*1.5)):
                record['selection']='rejected_for_latency_or_inference_failure'
                break
        record['passed']=len(record['cases'])==len(cases) and all(case['passed'] for case in record['cases'])
    except Exception as error:
        record['error']=repr(error); record['passed']=False
    finally:
        if process and process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL); process.wait()
        if log: log.close()
    if flags is not None and (WORK/(name+'.log')).exists():
        record['backend_log']=[line for line in (WORK/(name+'.log')).read_text(errors='replace').splitlines()
                               if any(word in line.lower() for word in ('offload','vulkan','clip using','clip_ctx:','model buffer'))][:100]
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--variant',action='append')
    args=parser.parse_args()
    WORK.mkdir(parents=True,exist_ok=True)
    output=WORK/'vlm-benchmark.json'
    results=json.loads(output.read_text()) if args.variant and output.exists() else []
    baseline_scene=None
    for row in results:
        if row['variant']=='vlm_existing_cpu' and row['cases']:
            baseline_scene=row['cases'][0]['seconds']
    variants=[('vlm_existing_cpu','http://127.0.0.1:8032',None),
              ('vlm_vulkan_vision',f'http://127.0.0.1:{PORT}',['--device','none','--n-gpu-layers','0','--no-kv-offload','--no-op-offload']),
              ('vlm_vulkan_all',f'http://127.0.0.1:{PORT+2}',['--device','Vulkan0','--n-gpu-layers','99'])]
    for name,base,flags in variants:
        if args.variant and name not in args.variant: continue
        result=run_variant(name,base,flags,baseline_scene); results.append(result)
        if name=='vlm_existing_cpu' and result['cases']:
            baseline_scene=result['cases'][0]['seconds']
        results=[r for r in results[:-1] if r['variant']!=name]+[result]
        output.write_text(json.dumps(results,ensure_ascii=False,indent=2))
        print(json.dumps({'variant':name,'passed':result.get('passed'),'error':result.get('error')}),flush=True)
