"""Download the approved fixed-shape NPU vision encoder with pinned hashes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request

WORK=Path.home()/'.local/state/codex-model-accel/20261117'
DEST=WORK/'npu-vision-candidate'
DEST.mkdir(exist_ok=True)
files=json.loads((WORK/'multimodal-catalog.json').read_text())['Data']['Files']
prefix='models/MultiModal/Qwen3-VL-4B-Instruct/'
parser=argparse.ArgumentParser()
parser.add_argument('--onnx-reference',action='store_true')
args=parser.parse_args()
names=[prefix+'model/vision.onnx'] if args.onnx_reference else [prefix+'vision.cix',prefix+'test_data/bear_resized.png',prefix+'model/LICENSE.txt']
selected=[item for item in files if item['Path'] in names]
assert len(selected)==len(names)
progress={'status':'downloading','files':[]}
status=WORK/('npu-vision-onnx-download.json' if args.onnx_reference else 'npu-vision-download.json')
status.write_text(json.dumps(progress,indent=2))

def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(4*1024*1024),b''): result.update(chunk)
    return result.hexdigest()

try:
    for item in selected:
        target=DEST/Path(item['Path']).name
        if not target.exists() or digest(target)!=item['Sha256']:
            url='https://modelscope.cn/models/cix/ai_model_hub_26_Q2/resolve/'+item['Revision']+'/'+urllib.parse.quote(item['Path'])
            chunk_size=8*1024*1024
            size=item['Size']
            def fetch(start):
                end=min(size,start+chunk_size)-1
                chunk=DEST/(target.name+'.chunk-'+str(start))
                if chunk.exists() and chunk.stat().st_size==end-start+1: return start,chunk
                last=None
                for attempt in range(3):
                    try:
                        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        request=urllib.request.Request(url,headers={'Range':f'bytes={start}-{end}'})
                        with opener.open(request,timeout=45) as response:
                            body=response.read(end-start+2)
                            if size>chunk_size: assert response.headers.get('Content-Range')==f'bytes {start}-{end}/{size}'
                        assert len(body)==end-start+1
                        chunk.write_bytes(body)
                        return start,chunk
                    except Exception as error:
                        last=error
                raise RuntimeError(f'Range {start} failed: {last!r}')
            part=target.with_suffix(target.suffix+'.part')
            with part.open('wb') as output:
                output.truncate(size)
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures=[pool.submit(fetch,start) for start in range(0,size,chunk_size)]
                    completed=0
                    for future in as_completed(futures):
                        start,chunk=future.result()
                        output.seek(start); output.write(chunk.read_bytes())
                        completed+=chunk.stat().st_size
                        progress['current']={'file':target.name,'completed':completed,'total':size}
                        status.write_text(json.dumps(progress,indent=2))
            assert digest(part)==item['Sha256'],'Downloaded hash mismatch'
            part.replace(target)
            for chunk in DEST.glob(target.name+'.chunk-*'): chunk.unlink()
        progress['files'].append({'path':str(target),'size':item['Size'],'sha256':item['Sha256'],'revision':item['Revision']})
    progress['status']='verified'
except Exception as error:
    progress['status']='failed'; progress['error']=repr(error)
    raise
finally:
    status.write_text(json.dumps(progress,indent=2))
