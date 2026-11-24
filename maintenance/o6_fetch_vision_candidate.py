"""Fetch small, checksum-verified reference files from the approved catalog."""
from pathlib import Path
import hashlib
import json
import urllib.parse
import urllib.request

WORK=Path.home()/'.local/state/codex-model-accel/20261117'
files=json.loads((WORK/'multimodal-catalog.json').read_text())['Data']['Files']
selected=[item for item in files if item['Path'].startswith('models/MultiModal/Qwen3-VL-4B-Instruct/')
          and item.get('Type')=='blob' and item.get('Size',10**9)<100_000
          and item['Path'].endswith(('.py','.md','.cfg','.json'))]
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
destination=WORK/'qwen3vl-npu-reference'
for item in selected:
    url='https://modelscope.cn/models/cix/ai_model_hub_26_Q2/resolve/'+item['Revision']+'/'+urllib.parse.quote(item['Path'])
    with opener.open(url,timeout=30) as response: body=response.read(100_001)
    assert hashlib.sha256(body).hexdigest()==item['Sha256']
    relative=Path(item['Path']).relative_to('models/MultiModal/Qwen3-VL-4B-Instruct')
    target=destination/relative; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(body)
    print('FILE '+str(relative)+'\n'+body.decode())
(WORK/'vision-candidate-files.json').write_text(json.dumps(selected,indent=2))
