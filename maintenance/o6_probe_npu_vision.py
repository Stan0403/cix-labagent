"""Verify real fixed-grid Qwen3-VL vision execution, not language generation."""
from pathlib import Path
import json
import os
import time
import numpy as np
from PIL import Image, ImageOps
from NOE_Engine import EngineInfer

WORK=Path.home()/'.local/state/codex-model-accel/20261117'
DEST=WORK/'npu-vision-candidate'
result={'purpose':'NPU encoder shape, timing and finite-output probe; not end-to-end recognition accuracy','runs':[]}
download=json.loads((WORK/'npu-vision-download.json').read_text())
assert download['status']=='verified'
image=ImageOps.pad(Image.open(DEST/'bear_resized.png').convert('RGB'),(320,320),method=Image.Resampling.BICUBIC,color=(128,128,128))
pixels=(np.asarray(image).astype(np.float32)/255-.5)/.5
chw=pixels.transpose(2,0,1)
# Qwen3-VL: merge 2x2 spatial patches, duplicate the single frame temporally.
patches=np.repeat(chw[None],2,axis=0).reshape(1,2,3,10,2,16,10,2,16)
patches=patches.transpose(0,3,6,4,7,2,1,5,8).reshape(400,1536).copy()
start=time.monotonic()
engine=EngineInfer(str(DEST/'vision.cix'))
result['load_seconds']=time.monotonic()-start
try:
    result['device_handles']=[os.readlink(fd) for fd in Path('/proc/self/fd').iterdir()
                              if fd.exists() and os.readlink(fd).startswith('/dev/aipu')]
    for i in range(3):
        start=time.monotonic()
        outputs=engine.forward([patches])
        elapsed=time.monotonic()-start
        stats=[{'size':int(np.asarray(value).size),'finite':bool(np.isfinite(value).all()),
                'min':float(np.min(value)),'max':float(np.max(value))} for value in outputs]
        result['runs'].append({'seconds':elapsed,'outputs':stats})
    result['passed']=bool(result['device_handles']) and all(len(run['outputs'])==4
        and all(value['finite'] and value['size']==100*2560 for value in run['outputs']) for run in result['runs'])
finally:
    engine.clean()
(WORK/'npu-vision-probe.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
