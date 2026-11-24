"""Render or install an independent, relocatable O6 user-service stack."""
import argparse
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import urllib.request

from verify_models import verify

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--models', type=Path, default=ROOT / 'models')
    p.add_argument('--cpu-server', type=Path, default=ROOT / '.deps/llama-cpu/build/bin/llama-server')
    p.add_argument('--npu-server', type=Path, default=ROOT / '.deps/llama-npu/build/bin/llama-server')
    p.add_argument('--asr-python', type=Path, default=ROOT / '.venv-asr/bin/python')
    p.add_argument('--npu-python', type=Path, default=ROOT / '.venv-npu/bin/python')
    p.add_argument('--apply', action='store_true', help='Install new cix-labagent-* user units and start them')
    p.add_argument('--render-only', action='store_true', help='Render without hardware/model checks; for review only')
    a = p.parse_args()
    if a.apply and a.render_only:
        p.error('--apply and --render-only are mutually exclusive')
    for name in ['models', 'cpu_server', 'npu_server', 'asr_python', 'npu_python']:
        setattr(a, name, getattr(a, name).resolve())
    python = ROOT / '.venv/bin/python'
    if not a.render_only:
        errors = verify(a.models)
        if platform.system() != 'Linux' or platform.machine() not in {'aarch64', 'arm64'}:
            errors.append('This profile requires Orion O6 ARM64 Linux')
        if not Path('/dev/aipu').exists():
            errors.append('Missing /dev/aipu; install the matching vendor board image/runtime')
        for f in [python, a.cpu_server, a.npu_server, a.asr_python, a.npu_python]:
            if not f.is_file():
                errors.append('Missing runtime: ' + str(f))
        if errors:
            raise SystemExit('\n'.join(errors))
        for executable, modules in [(a.asr_python, 'NOE_Engine,numpy,av,kaldi_native_fbank,sentencepiece,yaml'),
                                    (a.npu_python, 'NOE_Engine,numpy'), (python, 'NOE_Engine,onnxruntime,cv2,numpy')]:
            subprocess.run([str(executable), '-c', 'import ' + modules], check=True)
        # Refuse to take ports from an existing production stack.
        import socket
        for port in [8001, 8081, 8011, 8031, 8032, 8034, 8035]:
            with socket.socket() as sock:
                if sock.connect_ex(('127.0.0.1', port)) == 0:
                    raise SystemExit(f'Port {port} is already in use. Stop the previous stack explicitly first.')
    generated = ROOT / 'deploy/generated'
    generated.mkdir(exist_ok=True)
    model = a.models
    env = {
        'LABAGENT_HOST': '127.0.0.1', 'LABAGENT_PORT': '8001', 'LABAGENT_LOCAL_ONLY': 'true',
        'LABAGENT_CHAT_PROVIDER': 'api', 'LABAGENT_LLM_BASE_URL': 'http://127.0.0.1:8081/v1',
        'LABAGENT_LLM_MODEL': 'qwen3-4b-local', 'LABAGENT_OCR_PROVIDER': 'mnn',
        'LABAGENT_OCR_BASE_URL': 'http://127.0.0.1:8031', 'LABAGENT_VISION_PROVIDER': 'disabled',
        'LABAGENT_ASR_BASE_URL': 'http://127.0.0.1:8011/v1', 'LABAGENT_ASR_MODEL': 'sensevoice-small-cix',
        'LABAGENT_EVIDENCE_TTL_SECONDS': '900', 'LABAGENT_HTTP_TIMEOUT': '240',
        'LABAGENT_AGENT_MAX_TOKENS': '192', 'LABAGENT_METER_LIVE_ENABLED': 'true',
        'LABAGENT_EQUIPMENT_YOLO_SERVER': 'http://127.0.0.1:8035',
        'LABAGENT_METER_VLM_SERVER': 'http://127.0.0.1:8034',
        'LABAGENT_VLM_SERVER': 'http://127.0.0.1:8032',
        'LABAGENT_LOCAL_ASR_DIR': str(model / 'sensevoice-cix'), 'LABAGENT_LOCAL_ASR_PORT': '8011',
        'LABAGENT_YOLO_MODEL': str(model / 'equipment-yolo26-v1/equipment_yolo26n_960_heads_int8_bias32_packed.cix'),
        'LABAGENT_YOLO_BACKEND': 'cix', 'LABAGENT_YOLO_SIZE': '960', 'LABAGENT_YOLO_CONFIDENCE': '0.35',
        'LABAGENT_LCD_OCR_MODEL': str(model / 'lcd-v3/rec-lcd.onnx'),
        'LABAGENT_LCD_OCR_DICT': str(model / 'lcd-v3/ppocr_keys_v1.txt'),
        'LABAGENT_LCD_OCR_SHA256': 'ec0b3f0b6090c41720d1bcccc940103fffce74b6db5d1aa044229d40e8655d35',
        'LABAGENT_LCD_OCR_THREADS': '4', 'PYTHONUNBUFFERED': '1',
    }
    # JSON double quoting is compatible with systemd EnvironmentFile syntax for these paths.
    env_file = generated / 'o6.env'
    env_file.write_text(''.join(k + '=' + json.dumps(v) + '\n' for k, v in env.items()), encoding='utf-8')
    if os.name != 'nt':
        env_file.chmod(0o600)
    common = ['--host', '127.0.0.1', '--threads', '8', '--threads-batch', '8', '--parallel', '1',
              '--jinja', '--device', 'none', '--n-gpu-layers', '0', '--no-kv-offload', '--no-op-offload']
    vlm = ['--model', model / 'qwen3-vl-4b-gguf/Qwen3VL-4B-Instruct-Q4_K_M.gguf',
           '--mmproj', model / 'qwen3-vl-4b-gguf/mmproj-Qwen3VL-4B-Instruct-F16.gguf',
           '--alias', 'qwen3-vl-4b-local', '--ctx-size', '4096', '--temp', '0', '--n-predict', '512', '--no-mmproj-offload']
    units = {
      'llm': ([a.cpu_server, '--model', model / 'qwen3-4b/Qwen3-4B-Q4_K_M.gguf', '--alias', 'qwen3-4b-local',
               '--port', '8081', '--ctx-size', '8192', '--n-predict', '1536',
               '--chat-template-kwargs', '{"enable_thinking":false}'] + common, [], 'simple'),
      'vision-worker': ([a.npu_python, ROOT / 'scripts/cix_npu_vision_worker.py', '--model', model / 'qwen3-vl-4b-npu/vision.cix'], [], 'notify'),
      'meter-model': ([a.npu_server, '--port', '8034', '--image-min-tokens', '100', '--image-max-tokens', '100'] + vlm + common, ['vision-worker'], 'simple'),
      'vlm': ([a.cpu_server, '--port', '8032'] + vlm + common, [], 'simple'),
      'yolo': ([python, '-m', 'uvicorn', 'scripts.yolo_equipment_service:app', '--host', '127.0.0.1', '--port', '8035'], [], 'simple'),
      'asr': ([a.asr_python, '-m', 'labagent.speech_service'], [], 'simple'),
      'adapter': ([python, '-m', 'uvicorn', 'scripts.qwen3vl_gguf_service:app', '--host', '127.0.0.1', '--port', '8031'], ['vlm', 'yolo', 'meter-model'], 'simple'),
      'web': ([python, '-m', 'labagent'], ['llm', 'asr', 'adapter'], 'simple'),
    }
    for name, (command, deps, kind) in units.items():
        # Wrapper uses POSIX quoting; systemd only sees a constant bash command and a quoted path.
        script = generated / (name + '.sh')
        exports = ''
        if name == 'meter-model':
            exports = 'export LABAGENT_NPU_VISION_SOCKET="${XDG_RUNTIME_DIR}/labagent-npu-vision.sock"\n'
        if name in {'llm', 'meter-model', 'vlm'}:
            command = ['taskset', '-c', '0,1,6,7,8,9,10,11'] + command
        script.write_text('#!/bin/bash\nset -euo pipefail\n' + exports + 'exec ' + shlex.join(map(str, command)) + '\n', encoding='utf-8')
        dependency = ' '.join('cix-labagent-' + d + '.service' for d in deps)
        text = f'''[Unit]
Description=CIX LabAgent {name}
PartOf=cix-labagent.target
Wants={dependency}
After={dependency}
[Service]
Type={kind}
WorkingDirectory={json.dumps(str(ROOT))}
EnvironmentFile={json.dumps(str(env_file))}
ExecStart=/bin/bash {json.dumps(str(script))}
Restart=on-failure
RestartSec=5
TimeoutStartSec=180
TimeoutStopSec=30
UMask=0077
[Install]
WantedBy=cix-labagent.target
'''
        (generated / ('cix-labagent-' + name + '.service')).write_text(text, encoding='utf-8')
    wants = ' '.join('cix-labagent-' + n + '.service' for n in units)
    (generated / 'cix-labagent.target').write_text('[Unit]\nDescription=CIX LabAgent stack\nWants=' + wants + '\n[Install]\nWantedBy=default.target\n', encoding='utf-8')
    print('Rendered:', generated)
    if a.apply:
        target = Path.home() / '.config/systemd/user'
        target.mkdir(parents=True, exist_ok=True)
        for f in list(generated.glob('*.service')) + list(generated.glob('*.target')):
            if (target / f.name).exists():
                raise SystemExit('Refusing to overwrite an existing unit: ' + str(target / f.name))
        for f in list(generated.glob('*.service')) + list(generated.glob('*.target')):
            shutil.copy2(f, target / f.name)
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', '--user', 'enable', '--now', 'cix-labagent.target'], check=True)
        print('Services launched. Run: python deploy/check_stack.py --wait 180')


if __name__ == '__main__':
    main()
