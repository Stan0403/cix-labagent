"""Compare existing CPU, optimized CPU, and real Vulkan GPU without session writes."""
from pathlib import Path
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

WORK = Path.home()/'.local/state/codex-model-accel/20261117'
ROOT = Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
BINARY = Path.home()/'llama.cpp/build-codex-vulkan/bin/llama-server'
MODEL = ROOT/'models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf'
PORT = 18081
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
sys.path.insert(0, str(ROOT))
from labagent.agent import READ_TOOLS


def request(base, payload):
    req = urllib.request.Request(base+'/v1/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    start=time.monotonic()
    with OPENER.open(req,timeout=180) as response:
        result=json.load(response)
    return result,time.monotonic()-start


def wait_ready(base, process):
    start=time.monotonic()
    while time.monotonic()-start<240:
        if process.poll() is not None:
            raise RuntimeError('Candidate server exited: '+str(process.returncode))
        try:
            with OPENER.open(base+'/health',timeout=2) as response:
                if response.status==200: return time.monotonic()-start
        except Exception: pass
        time.sleep(.5)
    raise TimeoutError('Candidate startup timeout')


def cases():
    manual=(ROOT/'knowledge/battery_voltage.md').read_text()
    tool=next(t for t in READ_TOOLS if t['function']['name']=='get_status')
    return [
        ('short',{'messages':[{'role':'system','content':'你是中文实验助手，请简洁回答。'},
                             {'role':'user','content':'请用一句中文说明如何核对万用表的直流电压档。'}]}),
        ('manual',{'messages':[{'role':'system','content':'按资料回答，不能编造。只用三句话。\n'+manual},
                              {'role':'user','content':'根据这份实验材料，读数无法识别时应该如何处理？'}]}),
        ('tool',{'messages':[{'role':'system','content':'你是实验助手，使用提供的工具。'},
                            {'role':'user','content':'请调用 get_status 查询实验状态。'}],
                 'tools':[tool],'tool_choice':{'type':'function','function':{'name':'get_status'}}}),
    ]


def run_variant(name, base, flags=None):
    process=None; log=None; startup=0
    record={'variant':name,'requests':[]}
    try:
        if flags is not None:
            port=urlsplit(base).port
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                probe.bind(('127.0.0.1',port))
            env=os.environ.copy(); env['VK_ICD_FILENAMES']='/etc/vulkan/icd.d/mali.json'
            command=['taskset','-c','0,1,6,7,8,9,10,11',str(BINARY),'--model',str(MODEL),
                     '--alias','qwen3-4b-local','--host','127.0.0.1','--port',str(port),
                     '--ctx-size','8192','--threads','8','--threads-batch','8','--parallel','1',
                     '--jinja','--chat-template-kwargs','{"enable_thinking":false}','--temp','0',*flags]
            log=(WORK/(name+'.log')).open('w')
            process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
            record['command']=command
            startup=wait_ready(base,process)
        record['startup_seconds']=startup
        warm,_=request(base,{'model':'qwen3-4b-local','messages':[{'role':'user','content':'请只回答“就绪”。'}],
                            'max_tokens':16,'temperature':0,'seed':42,'cache_prompt':False})
        for repeat in range(2):
            for case,extra in cases():
                payload={'model':'qwen3-4b-local','temperature':0,'seed':42,'max_tokens':96,'cache_prompt':False,**extra}
                body,seconds=request(base,payload)
                message=body['choices'][0]['message']
                if case=='tool':
                    calls=message.get('tool_calls',[])
                    passed=len(calls)==1 and calls[0]['function']['name']=='get_status' and json.loads(calls[0]['function']['arguments'])=={}
                else:
                    text=message.get('content') or ''
                    passed=bool(text.strip()) and '<think>' not in text and not message.get('reasoning_content')
                record['requests'].append({'case':case,'repeat':repeat,'seconds':seconds,'passed':passed,
                                           'message':message,'usage':body.get('usage'),'timings':body.get('timings'),
                                           'finish_reason':body['choices'][0].get('finish_reason')})
                (WORK/'llm-benchmark-progress.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
        record['passed']=all(r['passed'] for r in record['requests'])
    except Exception as error:
        record['error']=repr(error); record['passed']=False
    finally:
        if process and process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL); process.wait()
        if log: log.close()
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--variant',action='append')
    args=parser.parse_args()
    WORK.mkdir(parents=True,exist_ok=True)
    output=WORK/'llm-benchmark.json'
    results=json.loads(output.read_text()) if args.variant and output.exists() else []
    variants=[('llm_existing_cpu','http://127.0.0.1:8081',None),
              ('llm_optimized_cpu',f'http://127.0.0.1:{PORT}',['--device','none','--n-gpu-layers','0','--no-kv-offload']),
              ('llm_vulkan',f'http://127.0.0.1:{PORT+1}',['--device','Vulkan0','--n-gpu-layers','99'])]
    for name,base,flags in variants:
        if args.variant and name not in args.variant: continue
        result=run_variant(name,base,flags); results.append(result)
        results=[r for r in results[:-1] if r['variant']!=name]+[result]
        output.write_text(json.dumps(results,ensure_ascii=False,indent=2))
        print(json.dumps({'variant':name,'passed':result.get('passed'),'error':result.get('error'),
                          'seconds':[round(x['seconds'],3) for x in result['requests']]}),flush=True)
