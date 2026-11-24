"""Offline full-frame -> automatic ROI -> trained OCR acceptance probe."""
import io
import json
from pathlib import Path
import sys
import time
from PIL import Image

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'LabAgent-demo'))
from labagent.lcd_reader import LcdReader,locate_lcd
from labagent.perception import crop_roi

reader=LcdReader(ROOT/'runs/lcd-v3-user81/rec-lcd.onnx',ROOT/'assets/ppocr_keys_v1.txt')
rows=[json.loads(x) for x in (ROOT/'data/user-captures-20261122/reviewed-labels.jsonl').read_text(encoding='utf-8').splitlines()]
results=[]
for row in rows:
    start=time.perf_counter();idx=row['review_id']
    image=Image.open(ROOT/f'data/user-captures-20261122/images/frame-{idx:03d}.jpg').convert('RGB')
    buffer=io.BytesIO();image.save(buffer,format='JPEG',quality=85)
    proposed=locate_lcd(Image.open(io.BytesIO(buffer.getvalue())).convert('RGB'))
    if proposed:
        result=reader.read(crop_roi(image,proposed))
    else:result={'text':'','accepted':False,'reason':'no_fast_roi'}
    results.append({'id':idx,'truth':row['label'],'roi':proposed,**result,'total_seconds':time.perf_counter()-start})
report={'cases':len(results),'fast_accepted':sum(r['accepted'] for r in results),
        'fast_correct':sum(r['accepted'] and r['text']==r['truth'] for r in results),
        'fast_wrong':[r for r in results if r['accepted'] and r['text']!=r['truth']],
        'fallback_cases':[r for r in results if not r['accepted']],'records':results,
        'limit':'New capture session was used in training; this is routing/geometry integration evidence, not independent model accuracy'}
(ROOT/'production-reader/full-frame-probe.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='records'}))
