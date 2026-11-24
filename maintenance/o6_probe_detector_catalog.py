"""Inspect the approved CIX catalog for deployable equipment-vision candidates."""
from pathlib import Path
import json
import urllib.parse
import urllib.request

WORK=Path.home()/'.local/state/codex-model-accel/20261117'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
repo='cix/ai_model_hub_26_Q2'
url='https://modelscope.cn/api/v1/models/'+repo+'/repo/files?'+urllib.parse.urlencode(
    {'Revision':'master','Root':'models/MultiModal','Recursive':'true'})
with opener.open(url,timeout=30) as response:
    data=response.read(4_000_001)
assert len(data)<=4_000_000
payload=json.loads(data)
(WORK/'multimodal-catalog.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
files=payload.get('Data',{}).get('Files',[])
selected=[item for item in files if any(name in item.get('Path','').lower()
                                      for name in ['yolo_world','qwen2.5-vl-3b'])]
for item in selected:
    print(json.dumps({k:item.get(k) for k in ['Path','Type','Size','Sha256','Revision']},ensure_ascii=False))
readmes=[item for item in selected if item.get('Type')=='blob' and item.get('Name','').lower() in ['readme.md','readme.txt']]
for item in readmes:
    path=item['Path']
    source='https://modelscope.cn/models/'+repo+'/resolve/master/'+urllib.parse.quote(path)
    with opener.open(source,timeout=30) as response: body=response.read(100_000).decode()
    local=WORK/('catalog-'+path.split('/')[-2]+'.md')
    local.write_text(body)
    print('GUIDE '+path+'\n'+body[:18000])
