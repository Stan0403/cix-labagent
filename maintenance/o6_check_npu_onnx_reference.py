"""Separate compiled-encoder deviation from the existing GGUF encoder deviation."""
from pathlib import Path
import json
import time
import subprocess
import numpy as np
import onnxruntime as ort

WORK=Path.home()/'.local/state/codex-model-accel/20261117'
while subprocess.run(['systemctl','--user','is-active','--quiet','codex-npu-vision-reference-download-20261117']).returncode==0:
    time.sleep(2)
assert json.loads((WORK/'npu-vision-onnx-download.json').read_text())['status']=='verified'
assert (WORK/'encoder-comparison.input.f32').exists()
pixels=np.fromfile(WORK/'encoder-comparison.input.f32',dtype='<f4').reshape(320,320,3)
chw=pixels.transpose(2,0,1)
patches=np.repeat(chw[None],2,axis=0).reshape(1,2,3,10,2,16,10,2,16)
patches=patches.transpose(0,3,6,4,7,2,1,5,8).reshape(400,1536).copy()
options=ort.SessionOptions(); options.intra_op_num_threads=8; options.inter_op_num_threads=1
started=time.monotonic()
session=ort.InferenceSession(str(WORK/'npu-vision-candidate/vision.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
result={'onnxruntime':ort.__version__,'load_seconds':time.monotonic()-started,'inputs':[(i.name,i.shape,i.type) for i in session.get_inputs()]}
started=time.monotonic()
values=session.run(None,{session.get_inputs()[0].name:patches})
result['forward_seconds']=time.monotonic()-started
reference=np.concatenate([value.reshape(100,2560) for value in values],axis=1).reshape(100,4,2560)
result['comparisons']={}
for name,filename in [('npu','encoder-npu-comparison.f32'),('gguf','encoder-cpu-comparison.f32')]:
    candidate=np.fromfile(WORK/filename,dtype='<f4').reshape(100,4,2560)
    checks=[]
    for i in range(4):
        left=reference[:,i].astype(np.float64).ravel(); right=candidate[:,i].astype(np.float64).ravel()
        checks.append({'stream':i,'cosine':float(left@right/(np.linalg.norm(left)*np.linalg.norm(right))),
                       'normalized_rmse':float(np.linalg.norm(left-right)/np.linalg.norm(left))})
    result['comparisons'][name]=checks
(WORK/'npu-onnx-reference-check.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
