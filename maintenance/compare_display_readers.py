"""Time stored-image LCD readers without creating or modifying experiment records."""
import asyncio
import io
import json
from pathlib import Path
import sys
import time
import urllib.request
import uuid

import cv2
import numpy as np
from PIL import Image

ROOT=Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
OUT=Path('/tmp/codex-display-readers-20261122')
OUT.mkdir(exist_ok=True)
sys.path.insert(0,str(ROOT))
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def request(path,raw,mime):
    boundary='codex-'+uuid.uuid4().hex
    payload=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="image"\r\nContent-Type: {mime}\r\n\r\n').encode()+raw+f'\r\n--{boundary}--\r\n'.encode()
    req=urllib.request.Request('http://127.0.0.1:8031/'+path,data=payload,
        headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    start=time.perf_counter()
    with opener.open(req,timeout=240) as response:
        result=json.load(response)
    return {'seconds':time.perf_counter()-start,'result':result}

report={'stored_image':True,'production_records_modified':False,
        'purpose':'single stored-camera-image integration and latency probe, not an accuracy benchmark'}
def save():
    (OUT/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))

image_path=ROOT.parent/'LabAgent/data/images/82b563446df13fa42a84c4e587c1b1379c84699f36f6011775881f9216d007e8.jpg'
report['locate']=request('locate_display',image_path.read_bytes(),'image/jpeg');save()
print('LOCATE',report['locate'],flush=True)
roi=report['locate']['result'].get('roi')
if not roi or len(roi)!=4:
    raise RuntimeError('No display ROI; no reader comparison made')
image=Image.open(image_path).convert('RGB')
x,y,w,h=roi
crop=image.crop((round(x*image.width),round(y*image.height),round((x+w)*image.width),round((y+h)*image.height)))
buffer=io.BytesIO();crop.save(buffer,format='PNG');raw=buffer.getvalue()
(OUT/'display.png').write_bytes(raw)
report['roi_size']=list(crop.size)
report['vlm']=request('recognize',raw,'image/png');save()
print('VLM',report['vlm'],flush=True)

from scripts import cix_ocr_service as service
from starlette.datastructures import UploadFile
start=time.perf_counter();service.startup();report['npu_load_seconds']=time.perf_counter()-start
try:
    async def full():
        file=UploadFile(io.BytesIO(raw),filename='display.png')
        try:
            return await service.recognize(file)
        finally:
            file.file.close()
    start=time.perf_counter();result=asyncio.run(full())
    report['ppocr_full']={'seconds':time.perf_counter()-start,'result':result,'http_included':False};save()
    print('PPOCR_FULL',report['ppocr_full'],flush=True)
    array=cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)
    report['ppocr_rec_only']=[]
    for _ in range(3):
        start=time.perf_counter()
        tensor=service.recognition_tensor(array)
        logits=np.asarray(service.models['rec'].forward([tensor])[0]).reshape(1,100,6625).astype(np.float32)
        probs=np.exp(logits-logits.max(axis=2,keepdims=True));probs/=probs.sum(axis=2,keepdims=True)
        text,confidence=service.TextRecognizer().postprocess_op(probs)[0]
        report['ppocr_rec_only'].append({'seconds':time.perf_counter()-start,'text':text,'confidence':float(confidence)})
    save();print('PPOCR_REC_ONLY',report['ppocr_rec_only'],flush=True)
finally:
    service.shutdown()
