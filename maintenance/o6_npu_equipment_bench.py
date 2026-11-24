"""Compare fixed-grid CPU/NPU vision numerically and on real equipment negatives."""
from pathlib import Path
import argparse
import importlib.util
import io
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.request

import numpy as np
from PIL import Image, ImageOps
from starlette.datastructures import UploadFile

WORK=Path.home()/'.local/state/codex-model-accel/20261117'
ROOT=Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
BINARY=Path.home()/'llama.cpp-codex-npu/build/bin/llama-server'
MODELS=ROOT/'models/qwen3-vl-4b-gguf'
IMAGE=ROOT/'data/images/4017346abd93f9916a9f8b1e009be3fb0eb9f1beaffff8ca06340a21ab570218.jpg'
sys.path[:0]=[str(ROOT),str(WORK)]
from o6_llm_accel_bench import wait_ready

spec=importlib.util.spec_from_file_location('npu_equipment_adapter',ROOT/'scripts/qwen3vl_gguf_service.py')
adapter=importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
original_infer=adapter.infer
LABELS={'battery':'单节干电池','multimeter':'数字万用表','probes':'红黑表笔'}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
result={'purpose':'fixed 320-square input; CPU/NPU at equal resolution, no experiment/session writes','variants':[]}
record=WORK/'npu-equipment-benchmark.json'


def save(): record.write_text(json.dumps(result,ensure_ascii=False,indent=2))
def square(image): return ImageOps.pad(image,(320,320),method=Image.Resampling.BICUBIC,color=(128,128,128))
def image_bytes(image):
    output=io.BytesIO(); image.save(output,format='PNG'); return output.getvalue()
def fixed_infer(image,instruction,tokens=256,max_side=896,deadline=None):
    return original_infer(square(image),instruction,tokens=tokens,max_side=320,deadline=deadline)
adapter.infer=fixed_infer


def terminate(process):
    if process and process.poll() is None:
        os.killpg(process.pid,signal.SIGTERM)
        try: process.wait(timeout=20)
        except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL); process.wait()


def launch(port,npu=False):
    env=os.environ.copy()
    env['LABAGENT_CLIP_DUMP_PREFIX']=str(WORK/'encoder-reference')
    if npu: env['LABAGENT_NPU_VISION_SOCKET']=str(WORK/'qwen3vl-npu.sock')
    else: env.pop('LABAGENT_NPU_VISION_SOCKET',None)
    command=['taskset','-c','0,1,6,7,8,9,10,11',str(BINARY),'--model',str(MODELS/'Qwen3VL-4B-Instruct-Q4_K_M.gguf'),
             '--mmproj',str(MODELS/'mmproj-Qwen3VL-4B-Instruct-F16.gguf'),'--alias','qwen3-vl-4b-local',
             '--host','127.0.0.1','--port',str(port),'--ctx-size','4096','--threads','8','--threads-batch','8',
             '--parallel','1','--jinja','--temp','0','--n-predict','512','--device','none','--n-gpu-layers','0',
             '--no-kv-offload','--no-op-offload','--no-mmproj-offload','--image-min-tokens','100','--image-max-tokens','100']
    log=(WORK/('equipment-'+('npu' if npu else 'cpu320')+'.log')).open('w')
    process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
    try: wait_ready(f'http://127.0.0.1:{port}',process)
    except Exception: terminate(process); log.close(); raise
    return process,log


def numerical_check():
    metadata=json.loads((WORK/'encoder-reference.json').read_text())
    assert metadata=={'width':320,'height':320,'output_floats':1024000},metadata
    pixels=(WORK/'encoder-reference.input.f32').read_bytes()
    baseline=np.fromfile(WORK/'encoder-reference.output.f32',dtype='<f4').reshape(100,4,2560)
    (WORK/'encoder-comparison.input.f32').write_bytes(pixels)
    baseline.tofile(WORK/'encoder-cpu-comparison.f32')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(30); client.connect(str(WORK/'qwen3vl-npu.sock'))
        client.sendall(struct.pack('<4I',0x51334e50,320,320,320*320*3)+pixels)
        def receive(size):
            chunks=[]
            while size:
                data=client.recv(min(size,1<<20))
                if not data: raise ConnectionError('Truncated NPU response')
                chunks.append(data); size-=len(data)
            return b''.join(chunks)
        magic,count=struct.unpack('<2I',receive(8))
        assert (magic,count)==(0x51334e50,1024000)
        candidate=np.frombuffer(receive(count*4),dtype='<f4').reshape(100,4,2560)
    candidate.tofile(WORK/'encoder-npu-comparison.f32')
    checks=[]
    for i in range(4):
        left=baseline[:,i].astype(np.float64).ravel(); right=candidate[:,i].astype(np.float64).ravel()
        cosine=float(left@right/(np.linalg.norm(left)*np.linalg.norm(right)))
        nrmse=float(np.linalg.norm(left-right)/np.linalg.norm(left))
        checks.append({'stream':i,'cosine':cosine,'normalized_rmse':nrmse,'passed':cosine>.99 and nrmse<.15})
    matrix=[]
    for i in range(4):
        left=baseline[:,i].astype(np.float64).ravel()
        matrix.append([float(left@candidate[:,j].astype(np.float64).ravel()/(np.linalg.norm(left)*np.linalg.norm(candidate[:,j].astype(np.float64)))) for j in range(4)])
    return {'streams':checks,'pairwise_cosine':matrix,'passed':all(x['passed'] for x in checks)}


def cases(name,port):
    adapter.SERVER=f'http://127.0.0.1:{port}'; adapter.BACKEND=name
    original=Image.open(IMAGE).convert('RGB')
    samples=[('scene',square(original),'inspect'),
             ('blank_negative',Image.new('RGB',(320,320),'white'),'inspect'),
             ('unlit_display_negative',square(original.crop((490,405,685,525))),'recognize'),
             ('missing_black_probe_negative',square(original.crop((0,0,1080,original.height))),'inspect')]
    row={'variant':name,'cases':[]}; result['variants'].append(row)
    for case,image,endpoint in samples:
        started=time.monotonic()
        try:
            file=UploadFile(file=io.BytesIO(image_bytes(image)),filename='scene.png')
            response=adapter.inspect(file,labels=json.dumps(LABELS,ensure_ascii=False)) if endpoint=='inspect' else adapter.recognize(file)
            if case=='scene': passed=all(response['present'].get(k) is True for k in LABELS) and len(response['boxes'])>=4
            elif case=='blank_negative': passed=not any(value is True for value in response['present'].values()) and not response['boxes']
            elif case=='missing_black_probe_negative': passed=response['present'].get('probes') is not True
            else:
                text=response['text'].strip()
                if text.startswith('```'): text=text.split('\n',1)[1].rsplit('```',1)[0]
                passed=json.loads(text).get('reading') is None
            item={'case':case,'seconds':time.monotonic()-started,'passed':passed,'response':response}
        except Exception as error:
            item={'case':case,'seconds':time.monotonic()-started,'passed':False,'error':repr(error)}
        row['cases'].append(item); save()
        print(json.dumps({'variant':name,'case':case,'seconds':item['seconds'],'passed':item['passed']}),flush=True)
    row['passed']=all(item['passed'] for item in row['cases']); save()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--diagnostic-continue',action='store_true',help='Measure behavior despite known numerical mismatch; never approve promotion')
    parser.add_argument('--numerical-only',action='store_true')
    args=parser.parse_args()
    if args.numerical_only: record=WORK/'npu-encoder-numerical-repeat.json'
    worker=server=None; worker_log=server_log=None
    try:
        while subprocess.run(['systemctl','--user','is-active','--quiet','codex-npu-bridge-build-20261117']).returncode==0:
            time.sleep(2)
        assert json.loads((WORK/'npu-bridge-build.json').read_text())['status']=='passed'
        worker_log=(WORK/'npu-vision-worker.log').open('w')
        worker=subprocess.Popen([str(ROOT.parent/'LabAgent/.venv-ocr/bin/python'),str(WORK/'npu_vision_worker.py')],
                                stdout=worker_log,stderr=subprocess.STDOUT,start_new_session=True)
        for _ in range(60):
            if worker.poll() is not None: raise RuntimeError('NPU worker exited')
            if (WORK/'qwen3vl-npu.sock').exists(): break
            time.sleep(.5)
        else: raise TimeoutError('NPU worker not ready')
        server,server_log=launch(18042)
        import base64
        photo=image_bytes(square(Image.open(WORK/'npu-vision-candidate/bear_resized.png').convert('RGB')))
        payload={'model':'qwen3-vl-4b-local','temperature':0,'max_tokens':24,'messages':[{'role':'user','content':[
            {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(photo).decode()}},
            {'type':'text','text':'Name the main animal. One word only.'}]}]}
        request=urllib.request.Request('http://127.0.0.1:18042/v1/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with opener.open(request,timeout=120) as response: result['reference_answer']=json.load(response)
        result['numerical_equivalence']=numerical_check(); save()
        result['eligible_for_promotion']=result['numerical_equivalence']['passed']
        if not result['numerical_equivalence']['passed'] and not args.diagnostic_continue:
            raise RuntimeError('NPU/CPU encoder numerical check failed')
        if not args.numerical_only:
            cases('cpu_fixed_320',18042)
            terminate(server); server=None; server_log.close(); server_log=None
            server,server_log=launch(18043,npu=True)
            cases('npu_vision_cpu_text_fixed_320',18043)
        result['status']='complete' if result['numerical_equivalence']['passed'] else 'diagnostic_complete_numerical_mismatch'
    except Exception as error:
        result['status']='failed'; result['error']=repr(error)
    finally:
        terminate(server); terminate(worker)
        if server_log: server_log.close()
        if worker_log: worker_log.close()
        save()
