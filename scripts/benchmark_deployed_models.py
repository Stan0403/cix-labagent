"""Serial HTTP latency samples for deployed models; never writes experiment sessions."""
import argparse
import hashlib
import json
import mimetypes
from pathlib import Path
import statistics
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def clean(value):
    if isinstance(value, dict):
        return {key: clean(v) for key, v in value.items() if key != 'image_url'}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def get(url):
    with OPENER.open(url, timeout=5) as response:
        return json.load(response)


def post(url, body, content_type, timeout=60):
    request = urllib.request.Request(url, data=body, headers={'Content-Type': content_type})
    started = time.perf_counter()
    try:
        with OPENER.open(request, timeout=timeout) as response:
            result = {'status': response.status, 'response': clean(json.load(response))}
    except Exception as exc:
        result = {'error': type(exc).__name__, 'detail': str(exc)}
    result['http_seconds'] = time.perf_counter() - started
    return result


def multipart(path, fields):
    boundary = 'labagent-benchmark-' + uuid.uuid4().hex
    data = bytearray()
    for name, value in fields.items():
        data.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    data.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample"\r\nContent-Type: {content_type}\r\n\r\n'.encode())
    data.extend(path.read_bytes())
    data.extend(f'\r\n--{boundary}--\r\n'.encode())
    return bytes(data), 'multipart/form-data; boundary=' + boundary


def image_info(path):
    raw = path.read_bytes()
    return {'name': path.name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def aggregate(rows):
    values = [row['http_seconds'] for row in rows if row.get('status') == 200]
    return {'n': len(rows), 'successful_http': len(values),
            'mean_seconds': statistics.mean(values) if values else None,
            'median_seconds': statistics.median(values) if values else None,
            'min_seconds': min(values) if values else None, 'max_seconds': max(values) if values else None}


def benchmark(root, scene=None, repeats=2):
    result = {'date': time.strftime('%Y-%m-%d'), 'sampled_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
              'serial_client': True, 'model_services_already_loaded': True,
              'experiment_session_api_called': False, 'accuracy_benchmark': False,
              'llm': {}, 'ocr_fast_only': {}, 'limits': [
                  'Small latency samples, not an independent accuracy or P95 study.',
                  'HTTP times exclude model loading, browser capture, UI countdown and session/database writes.',
                  'OCR fixtures are development diagnostics; scene image is identified by SHA-256, not published here.']}
    cases = {
      'short': {'messages': [{'role': 'user', 'content': '用一句话说明万用表直流电压档的作用。'}]},
      'manual': {'messages': [{'role': 'user', 'content': '实验说明：测量单节电池电压。黑表笔接COM，红表笔接V/Ω，使用直流电压档。先核对量程与表笔接线，再接触电池两端。若屏幕模糊、数字或小数点不清，应调整光照和相机，不得猜测读数。请用两句话概括读数前要核对什么。'}]},
      'tool': {'messages': [{'role': 'user', 'content': '调用get_status查询当前实验状态，不要回答其他内容。'}],
               'tools': [{'type': 'function', 'function': {'name': 'get_status', 'description': '读取实验状态',
                          'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}}],
               'tool_choice': {'type': 'function', 'function': {'name': 'get_status'}}},
    }
    for name, body in cases.items():
        payload = {'model': 'qwen3-4b-local', 'temperature': 0, 'seed': 42, 'max_tokens': 64,
                   'cache_prompt': False, 'chat_template_kwargs': {'enable_thinking': False}, **body}
        rows = []
        for repeat in range(repeats):
            row = post('http://127.0.0.1:8081/v1/chat/completions', json.dumps(payload).encode(), 'application/json')
            row['repeat'] = repeat
            rows.append(row)
        result['llm'][name] = {'request': payload, 'samples': rows, 'summary': aggregate(rows)}
    for name in ['leading_decimal_419.png', 'negative_zero.png', 'reading_1_353.png']:
        path = root / 'tests/fixtures/lcd' / name
        if not path.is_file():
            continue
        rows = []
        for repeat in range(3):
            body, content_type = multipart(path, {'fast_only': 'true'})
            row = post('http://127.0.0.1:8031/recognize', body, content_type, timeout=15)
            row['repeat'] = repeat
            rows.append(row)
        result['ocr_fast_only'][name] = {'input': image_info(path), 'samples': rows, 'summary': aggregate(rows)}
    if scene:
        result['scene_input'] = image_info(scene)
        labels = json.dumps({'multimeter': '数字万用表', 'battery': '电池', 'red_probe': '红表笔', 'black_probe': '黑表笔'}, ensure_ascii=False)
        result['yolo_health_before'] = get('http://127.0.0.1:8035/health')
        body, content_type = multipart(scene, {'labels': labels})
        result['yolo_warmup'] = post('http://127.0.0.1:8035/inspect', body, content_type)
        rows = [post('http://127.0.0.1:8035/inspect', body, content_type) for _ in range(5)]
        result['yolo'] = {'samples': rows, 'summary': aggregate(rows)}
        result['yolo_health_after'] = get('http://127.0.0.1:8035/health')
        worker_file = Path('/run/user/1000/labagent-npu-vision.json')
        worker_before = json.loads(worker_file.read_text()) if worker_file.is_file() else None
        rows = []
        for _ in range(2):
            body, content_type = multipart(scene, {})
            rows.append(post('http://127.0.0.1:8031/check_meter_live', body, content_type, timeout=75))
        worker_after = json.loads(worker_file.read_text()) if worker_file.is_file() else None
        result['meter'] = {'samples': rows, 'summary': aggregate(rows),
                           'npu_worker_before': worker_before, 'npu_worker_after': worker_after,
                           'npu_request_delta': worker_after['requests']-worker_before['requests'] if worker_before and worker_after else None}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--scene', type=Path, help='Existing equipment photo; only its hash is saved')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    report = benchmark(args.root.resolve(), args.scene, args.repeats)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
        print(args.output)
    else:
        print(text)
