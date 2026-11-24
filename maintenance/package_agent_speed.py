"""Package source and a checksum manifest against the last verified board snapshot."""
import hashlib
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'LabAgent-demo'
output=ROOT/'agent_optimization/2026-11-22'
output.mkdir(parents=True,exist_ok=True)
inventory=[r for r in json.loads((ROOT/'source-inventory.json').read_text(encoding='utf-8'))
           if r['project']=='LabAgent-demo']
old={r['path'][len('LabAgent-demo/'):]:r['sha256'] for r in inventory}
names=sorted(set(old)|{'tests/test_agent_decision_optimization.py','scripts/benchmark_agent_decisions.py'})
manifest={'files':[{'path':name,'baseline_sha256':old.get(name),
                    'sha256':hashlib.sha256((source/name).read_bytes()).hexdigest()}
                   for name in names if hashlib.sha256((source/name).read_bytes()).hexdigest()!=old.get(name)]}
(output/'optimization-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
with tarfile.open(output/'candidate.tar.gz','w:gz') as archive:
    for name in names:
        archive.add(source/name,arcname=name,recursive=False)
    archive.add(output/'optimization-manifest.json',arcname='optimization-manifest.json')
print('PACKAGED',len(names),'sources,',len(manifest['files']),'changed files')
