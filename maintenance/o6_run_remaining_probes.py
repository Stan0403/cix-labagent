"""Run remaining candidate probes sequentially to avoid resource contention."""
from pathlib import Path
import json
import subprocess
import sys
import time

work=Path.home()/'.local/state/codex-model-accel/20261117'
results=[]
for name,command in [
    ('vlm',[sys.executable,str(work/'o6_vlm_accel_bench.py'),'--variant','vlm_vulkan_vision','--variant','vlm_vulkan_all']),
    ('npu_ocr',[sys.executable,str(work/'o6_npu_ocr_probe.py')]),
]:
    start=time.monotonic()
    process=subprocess.run(command,timeout=1800)
    results.append({'probe':name,'exit':process.returncode,'seconds':time.monotonic()-start})
    (work/'remaining-probes.json').write_text(json.dumps(results,indent=2))
