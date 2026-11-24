"""Recompute reported aggregates from preserved original JSON evidence."""
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]


def summary():
    source = ROOT / 'evidence/model_acceleration/2026-11-17/llm-benchmark.json'
    language = {}
    for variant in json.loads(source.read_text(encoding='utf-8')):
        grouped = {}
        for request in variant['requests']:
            grouped.setdefault(request['case'], []).append(request['seconds'])
        language[variant['variant']] = {case: {'n': len(values), 'mean_seconds': statistics.mean(values)}
                                       for case, values in grouped.items()}
    ocr = json.loads((ROOT / 'evidence/ocr_training/production-reader/native-report.json').read_text(encoding='utf-8'))
    meter = json.loads((ROOT / 'evidence/meter_setup_review/2026-11-24/final/validation/meter-pipeline.json').read_text(encoding='utf-8'))
    return {
        'language_http_complete_response': language,
        'ocr_development_set': {key: ocr[key] for key in ['cases', 'fast_accepted', 'fast_wrong',
           'fast_pipeline_median_seconds', 'fast_pipeline_p90_seconds', 'providers', 'model_sha256']},
        'meter_retained_images': [{'case': c['case'], 'seconds': c.get('seconds')} for c in meter['cases']],
        'limits': ['Two repeats per language case; outputs can differ.',
                   'OCR photos participated in training; no independent accuracy claim.',
                   'Meter retained images have no independent physical ground truth.',
                   'No peak power, energy, sustained concurrency, or end-to-end classroom benchmark.']
    }


if __name__ == '__main__':
    print(json.dumps(summary(), ensure_ascii=False, indent=2))
