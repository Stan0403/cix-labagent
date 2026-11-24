"""Isolate detection, orientation and recognition geometry on existing NPU weights."""
from pathlib import Path
import hashlib
import json
import math
import sys
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
WORK = Path.home()/'.local/state/codex-model-accel/20261117'
sys.path.insert(0, str(ROOT))
from scripts import cix_ocr_service as service


def tensor(image, mode):
    if mode == 'stretch':
        return service.TextRecognizer().resize_norm_img(image)[None]
    height, width = image.shape[:2]
    resized_width = min(400, math.ceil(32*width/height))
    resized = cv2.resize(image, (resized_width, 32)).astype(np.float32).transpose(2,0,1)/127.5-1
    padded = np.full((3,32,400), 0 if mode=='aspect_zero' else 1, dtype=np.float32)
    padded[:,:,:resized_width] = resized
    return padded[None]


def recognize(image, mode):
    raw = np.asarray(service.models['rec'].forward([tensor(image, mode)])[0])
    logits = raw.reshape(1,100,6625).astype(np.float32)
    probabilities = np.exp(logits-logits.max(axis=2,keepdims=True))
    probabilities /= probabilities.sum(axis=2,keepdims=True)
    text, confidence = service.TextRecognizer().postprocess_op(probabilities)[0]
    return {'text':text, 'confidence':float(confidence), 'raw_shape':list(raw.shape),
            'raw_min':float(logits.min()), 'raw_max':float(logits.max()),
            'raw_class_sum_mean':float(logits.sum(axis=2).mean())}


def detect(image):
    scale = min(720/image.shape[1],1150/image.shape[0])
    resized = cv2.resize(image,(round(image.shape[1]*scale),round(image.shape[0]*scale)))
    canvas = np.full((1150,720,3),255,np.uint8)
    y,x = (1150-resized.shape[0])//2,(720-resized.shape[1])//2
    canvas[y:y+resized.shape[0],x:x+resized.shape[1]] = resized
    detector=service.TextDetector()
    data, shape=detector.preprocess_det(canvas)
    raw=service.models['det'].forward([data])[0]
    boxes=service.sorted_boxes(detector.postprocess_det(np.reshape(raw,(1,1,960,608)),shape,canvas))
    crops=[service.get_rotate_crop_image(canvas,box.copy()) for box in boxes]
    return boxes,crops


result={'purpose':'isolated preprocessing ablation, synthetic fonts only; no real experiment writes','cases':[]}
started=time.monotonic()
service.startup()
try:
    recognizer=service.TextRecognizer()
    result['dictionary_classes']=len(recognizer.postprocess_op.character)
    result['assets']={name:hashlib.sha256((service.MODEL/name).read_bytes()).hexdigest()
                      for name in ['pp_ocr.py','ppocr_keys_v1.txt','rec.cix']}
    for font_name in ['DejaVuSansMono.ttf','DejaVuSans.ttf']:
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/'+font_name,76)
        for text in ['1.34','-0.12','0.00','12.34','1234567890','HELLO']:
            image=Image.new('RGB',(720 if len(text)>7 else 480,160),'white')
            draw=ImageDraw.Draw(image)
            draw.text((25,25),text,font=font,fill='black')
            array=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2BGR)
            left,top,right,bottom=draw.textbbox((25,25),text,font=font)
            direct=array[max(0,top-8):bottom+8,max(0,left-8):right+8].copy()
            boxes,crops=detect(array)
            item={'font':font_name,'expected':text,'boxes':[box.tolist() for box in boxes],
                  'direct_known_crop':{},'detected_crops':[]}
            for mode in ['stretch','aspect_zero','aspect_white']:
                item['direct_known_crop'][mode]=recognize(direct,mode)
            if crops:
                classifier=service.TextClassifier()
                classifier.preprocess_cls(crops)
                for i in range(len(crops)):
                    cls_raw=np.asarray(service.models['cls'].forward([classifier.preprocess_cls_onnx(crops,i)])[0]).reshape(1,2)
                    classifier.postprocess_cls(cls_raw,classifier.indices,i,classifier.cls_res,crops)
                for i,crop in enumerate(crops):
                    output={'shape':list(crop.shape),'orientation':[str(classifier.cls_res[i][0]),float(classifier.cls_res[i][1])],
                            'modes':{mode:recognize(crop,mode) for mode in ['stretch','aspect_zero','aspect_white']}}
                    item['detected_crops'].append(output)
            result['cases'].append(item)
    result['summary']={}
    for mode in ['stretch','aspect_zero','aspect_white']:
        direct=sum(c['direct_known_crop'][mode]['text']==c['expected'] for c in result['cases'])
        pipeline=sum(' '.join(x['modes'][mode]['text'] for x in c['detected_crops'])==c['expected'] for c in result['cases'])
        result['summary'][mode]={'known_crop_exact':direct,'pipeline_exact':pipeline,'total':len(result['cases'])}
finally:
    service.shutdown()
result['elapsed_seconds']=time.monotonic()-started
(WORK/'ocr-diagnosis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({'summary':result['summary'],'seconds':result['elapsed_seconds']},ensure_ascii=False))
