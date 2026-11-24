"""Check the already-converted PP-OCRv4 NPU models without changing app routing."""
from pathlib import Path
import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

from PIL import Image, ImageDraw, ImageFont

ROOT=Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
WORK=Path.home()/'.local/state/codex-model-accel/20261117'
PORT=18021
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def recognize(data):
    boundary='codex-'+uuid.uuid4().hex
    payload=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="display.png"\r\nContent-Type: image/png\r\n\r\n').encode()+data+f'\r\n--{boundary}--\r\n'.encode()
    request=urllib.request.Request(f'http://127.0.0.1:{PORT}/recognize',data=payload,
                                  headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    start=time.monotonic()
    with OPENER.open(request,timeout=45) as response: result=json.load(response)
    return result,time.monotonic()-start


def png(image):
    out=io.BytesIO(); image.save(out,format='PNG'); return out.getvalue()


if __name__=='__main__':
    with socket.socket() as probe: probe.bind(('127.0.0.1',PORT))
    log=(WORK/'npu-ocr.log').open('w')
    binary=ROOT.parent/'LabAgent/.venv-ocr/bin/python'
    process=subprocess.Popen([str(binary),'-m','uvicorn','scripts.cix_ocr_service:app','--host','127.0.0.1','--port',str(PORT)],
                             cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    results={'purpose':'NPU execution and synthetic string smoke check; not real LCD accuracy validation','cases':[]}
    try:
        start=time.monotonic()
        while time.monotonic()-start<40:
            if process.poll() is not None: raise RuntimeError('NPU OCR service exited')
            try:
                with OPENER.open(f'http://127.0.0.1:{PORT}/health',timeout=2) as response:
                    results['health']=json.load(response)
                    if results['health'].get('ready'): break
            except Exception: pass
            time.sleep(.5)
        else: raise TimeoutError('NPU OCR startup timeout')
        handles=[]
        for fd in Path(f'/proc/{process.pid}/fd').iterdir():
            try: target=os.readlink(fd)
            except OSError: continue
            if '/dev/aipu' in target: handles.append(target)
        results['npu_device_handles']=handles
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',76)
        cases=[]
        for text in ['1.34','-0.12','0.00']:
            image=Image.new('RGB',(480,160),'white'); draw=ImageDraw.Draw(image)
            draw.text((25,25),text,font=font,fill='black')
            cases.append(('synthetic_'+text,png(image),text))
        cases.append(('blank',png(Image.new('RGB',(480,160),'white')),''))
        original=ROOT/'data/images/4017346abd93f9916a9f8b1e009be3fb0eb9f1beaffff8ca06340a21ab570218.jpg'
        with Image.open(original) as image:
            cases.append(('real_unlit_display',png(image.crop((490,405,685,525))),None))
        for name,data,expected in cases:
            body,seconds=recognize(data)
            item={'case':name,'expected_synthetic_text':expected,'seconds':seconds,'result':body}
            if expected is not None: item['synthetic_exact_match']=body.get('text','').replace(' ','')==expected
            results['cases'].append(item)
            (WORK/'npu-ocr-probe.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
        print(json.dumps({'ready':True,'device_handles':len(handles),'case_seconds':[round(c['seconds'],3) for c in results['cases']]}),flush=True)
    except Exception as error:
        results['error']=repr(error); print(json.dumps({'error':repr(error)}),flush=True)
    finally:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try: process.wait(timeout=15)
            except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL); process.wait()
        log.close()
        (WORK/'npu-ocr-probe.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
