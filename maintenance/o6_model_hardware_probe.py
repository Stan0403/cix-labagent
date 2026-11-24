"""Read-only model acceleration inventory; run on the O6."""
import json
from pathlib import Path
import subprocess

ROOT = Path('/home/radxa/referenced-chatgpt-conversation-this-is-an')
WORK = Path.home()/'.local/state/codex-model-accel/20261117'
WORK.mkdir(parents=True, exist_ok=True)
CHECKS = {
    'clinfo': ['clinfo', '-l'],
    'vulkan': ['sh', '-c', 'if [ -x /usr/bin/vulkaninfo ]; then /usr/bin/vulkaninfo --summary; else echo vulkaninfo-unavailable; fi'],
    'gpu_libraries': ['sh', '-c', '/usr/sbin/ldconfig -p | grep -Ei "OpenCL|vulkan|mali|noe"'],
    'cix_bins': ['ls', '-lh', '/usr/share/cix/bin'],
    'llama_gpu_build': ['sh', '-c', 'grep -E "GGML_(VULKAN|OPENCL|CPU_KLEIDIAI|CPU_ARM_ARCH)|CMAKE_BUILD_TYPE" /home/radxa/llama.cpp/build/CMakeCache.txt; find /home/radxa/llama.cpp -maxdepth 4 -type f -name llama-server'],
    'llama_devices': ['/home/radxa/llama.cpp/build/bin/llama-server', '--list-devices'],
    'npu_packages': ['sh', '-c', 'dpkg -l | grep -Ei "noe|npu|cix-drivers|opencl|vulkan|mali"'],
    'build_packages': ['sh', '-c', 'dpkg -l | grep -Ei "cmake|vulkan-dev|glsl|spirv|opencl.*dev|ninja|shaderc"'],
    'llama_source': ['sh', '-c', 'git -C /home/radxa/llama.cpp rev-parse HEAD; git -C /home/radxa/llama.cpp status --short'],
}
results = {}
for name, command in CHECKS.items():
    try:
        r = subprocess.run(command, capture_output=True, text=True, timeout=35)
        results[name] = {'exit': r.returncode, 'out': r.stdout, 'err': r.stderr}
    except Exception as error:
        results[name] = {'error': str(error)}

models = ROOT/'LabAgent/models'
results['models'] = [
    {'path': str(p.relative_to(models)), 'bytes': p.stat().st_size}
    for p in models.rglob('*') if p.is_file()
    and (p.suffix in {'.gguf', '.cix'} or p.name == 'config.json')
]
keys = {'LABAGENT_LLM_BASE_URL', 'LABAGENT_LLM_MODEL', 'LABAGENT_ASR_BASE_URL',
        'LABAGENT_ASR_MODEL', 'LABAGENT_OCR_BASE_URL', 'LABAGENT_OCR_PROVIDER',
        'LABAGENT_VLM_BASE_URL', 'LABAGENT_VLM_MODEL'}
results['configured_endpoints'] = {}
for line in (ROOT/'LabAgent-demo/.env').read_text().splitlines():
    key, separator, value = line.partition('=')
    if separator and key in keys:
        results['configured_endpoints'][key] = value
results['services'] = {}
for service in ['labagent-demo-llm', 'labagent-demo-asr', 'labagent-demo-vlm', 'labagent-qwen3vl4b']:
    r = subprocess.run(['systemctl', '--user', 'show', service, '-p', 'MainPID', '--value'],
                       capture_output=True, text=True, check=True)
    pid = int(r.stdout.strip() or 0)
    if not pid:
        continue
    args = Path(f'/proc/{pid}/cmdline').read_bytes().decode().split('\0')
    allow = {'--model', '--mmproj', '--alias', '--host', '--port', '--ctx-size',
             '--threads', '--threads-batch', '--parallel', '--n-gpu-layers'}
    filtered = [args[0]]
    for index, value in enumerate(args[1:]):
        if value in allow and index+2 < len(args):
            filtered.extend([value, args[index+2]])
        if value in {'--no-mmproj-offload'}:
            filtered.append(value)
    results['services'][service] = {'pid': pid, 'args': filtered}

path = WORK/'hardware-runtime.json'
path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
print(json.dumps({'saved': str(path), 'model_files': len(results['models'])}))
