"""Read-only native full-frame reader benchmark before activating the new adapter."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import time
import urllib.request
import uuid
import numpy as np
from PIL import Image

ROOT=Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
OUT=Path('/tmp/codex-lcd-reader-native');OUT.mkdir(exist_ok=True)
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('labagent.lcd_reader_candidate','/tmp/lcd_reader_candidate.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
from labagent.perception import crop_roi

model=ROOT/'data/ocr-training/20261122-user81'
start=time.perf_counter();reader=module.LcdReader(model/'rec-lcd.onnx',model/'ppocr_keys_v1.txt');load_seconds=time.perf_counter()-start
assert reader.model_sha256=='ec0b3f0b6090c41720d1bcccc940103fffce74b6db5d1aa044229d40e8655d35'
batch=ROOT/'data/ocr-capture/20261122_191101_3fbaeb8d'
rows=[json.loads(line) for line in (batch/'reviews/manual-review-user81.jsonl').read_text().splitlines()]
records=[]
for row in rows:
    start=time.perf_counter();image=Image.open(batch/row['path']).convert('RGB')
    buffer=io.BytesIO();image.save(buffer,format='JPEG',quality=85)
    roi=module.locate_lcd(Image.open(io.BytesIO(buffer.getvalue())).convert('RGB'))
    result=reader.read(crop_roi(image,roi)) if roi else {'text':'','accepted':False,'reason':'no_fast_roi'}
    record={'id':row['review_id'],'truth':row['label'],'roi':roi,**result,'total_seconds':time.perf_counter()-start}
    if result['accepted'] and result['text']!=row['label']:raise AssertionError(record)
    records.append(record)
    if len(records)%20==0:print('processed',len(records),flush=True)
legacy=[]
for idx,truth in [(2,'1.34'),(5,'1.353'),(6,'.419'),(10,'-0.00')]:
    path=Path(f'/tmp/codex-ocr-training-inventory/candidate-{idx:03d}.png')
    result=reader.read(Image.open(path));legacy.append({'id':idx,'truth':truth,**result})
assert legacy[2]['accepted'] is False,'Known decimal shift must not be fast accepted'

def old_http(image):
    buffer=io.BytesIO();image.save(buffer,format='PNG');boundary='probe-'+uuid.uuid4().hex
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="display.png"\r\nContent-Type: image/png\r\n\r\n').encode()+buffer.getvalue()+f'\r\n--{boundary}--\r\n'.encode()
    request=urllib.request.Request('http://127.0.0.1:8031/recognize',data=body,headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    start=time.perf_counter()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request,timeout=60) as response:result=json.load(response)
    return {'seconds':time.perf_counter()-start,'result':result}

baseline=[]
row=rows[1];baseline.append(old_http(Image.open(batch/row['path']).crop(row['crop_xyxy'])))
baseline.append(old_http(Image.open('/tmp/codex-ocr-training-inventory/candidate-006.png')))
fast=[r['total_seconds'] for r in records if r['accepted']]
report={'native_host':'O6 aarch64','providers':reader.session.get_providers(),'model_sha256':reader.model_sha256,
    'load_seconds':load_seconds,'cases':len(records),'fast_accepted':sum(r['accepted'] for r in records),
    'fast_wrong':[],'fast_pipeline_median_seconds':float(np.median(fast)),
    'fast_pipeline_p90_seconds':float(np.percentile(fast,90)),'legacy':legacy,'old_http_reading_baseline':baseline,
    'records':records,'limit':'81 training images reused for integration and latency; not independent model accuracy',
    'production_records_modified':False}
(OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in report.items() if k!='records'},ensure_ascii=False),flush=True)
