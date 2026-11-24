"""Build an isolated GPU runtime from the board's existing llama.cpp revision."""
from pathlib import Path
import json
import hashlib
import io
import os
import subprocess
import tarfile
import time
import urllib.request

WORK = Path.home()/'.local/state/codex-model-accel/20261117'
SOURCE = Path.home()/'llama.cpp'
BUILD = SOURCE/'build-codex-vulkan'
WORK.mkdir(parents=True, exist_ok=True)
env = os.environ.copy()
env['VK_ICD_FILENAMES'] = '/etc/vulkan/icd.d/mali.json'
HEADERS = WORK/'Vulkan-Headers-1.3.296'
if not (HEADERS/'include/vulkan/vulkan.hpp').exists():
    url = 'https://codeload.github.com/KhronosGroup/Vulkan-Headers/tar.gz/refs/tags/v1.3.296'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=90) as response:
        archive = response.read(16_000_001)
    if len(archive)>16_000_000:
        raise RuntimeError('Unexpected header archive size')
    (WORK/'vulkan-headers-source.json').write_text(json.dumps({'url':url,'sha256':hashlib.sha256(archive).hexdigest()},indent=2))
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as source_archive:
        for member in source_archive:
            if not member.isfile():
                continue
            target=(WORK/member.name).resolve()
            if not target.is_relative_to(HEADERS):
                raise ValueError('Unexpected header archive member')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(source_archive.extractfile(member).read())
commands = [
    ['cmake', '-S', str(SOURCE), '-B', str(BUILD), '-DCMAKE_BUILD_TYPE=Release',
     '-DGGML_VULKAN=ON', '-DGGML_NATIVE=OFF', '-DGGML_CPU_KLEIDIAI=ON',
     '-DGGML_CPU_ARM_ARCH=armv9-a+i8mm+dotprod+sve',
     '-DVulkan_INCLUDE_DIR='+str(HEADERS/'include'),
     '-DFETCHCONTENT_SOURCE_DIR_KLEIDIAI_DOWNLOAD='+str(SOURCE/'build/_deps/kleidiai_download-src')],
    ['cmake', '--build', str(BUILD), '--target', 'llama-server', 'llama-bench', '-j4'],
    [str(BUILD/'bin/llama-server'), '--list-devices'],
]
results = {'source_commit': subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip(),
           'build': str(BUILD), 'status': 'running', 'steps': []}
status = WORK/'vulkan-build.json'
if (WORK/'vulkan-build.log').exists() and not (WORK/'vulkan-build-first.log').exists():
    (WORK/'vulkan-build.log').rename(WORK/'vulkan-build-first.log')
status.write_text(json.dumps(results, indent=2))
with (WORK/'vulkan-build.log').open('w') as log:
    for command in commands:
        start = time.monotonic()
        log.write('\nCOMMAND '+repr(command)+'\n'); log.flush()
        r = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=1800)
        results['steps'].append({'command': command, 'exit': r.returncode, 'seconds': time.monotonic()-start})
        status.write_text(json.dumps(results, indent=2))
        if r.returncode:
            results['status'] = 'failed'
            status.write_text(json.dumps(results, indent=2))
            raise SystemExit(r.returncode)
results['status'] = 'passed'
status.write_text(json.dumps(results, indent=2))
