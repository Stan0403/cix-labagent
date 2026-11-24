"""Keep completed GPU measurements if an uncompetitive test is stopped early."""
import json
from pathlib import Path
import subprocess

work=Path.home()/'.local/state/codex-model-accel/20261117'
unit='codex-llm-gpu-bench-20261117'
active=subprocess.run(['systemctl','--user','is-active','--quiet',unit]).returncode==0
if active:
    subprocess.run(['systemctl','--user','stop',unit],check=True)
    progress=json.loads((work/'llm-benchmark-progress.json').read_text())
    assert progress['variant']=='llm_vulkan'
    progress.update(stopped_early=True,selection='rejected_for_latency',
                    reason='GPU prompt processing consistently much slower than optimized CPU',
                    passed=all(r['passed'] for r in progress['requests']))
    results=json.loads((work/'llm-benchmark.json').read_text())
    results=[r for r in results if r['variant']!='llm_vulkan']+[progress]
    (work/'llm-benchmark.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print('GPU_BENCH_STOPPED_WITH_RECORDS',len(progress['requests']))
else:
    print('GPU_BENCH_ALREADY_FINISHED')
