"""Compare real Agent choices on synthetic states in an isolated SQLite database."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--variant', required=True)
parser.add_argument('--resume', action='store_true')
parser.add_argument('--cases', nargs='*')
parser.add_argument('--format', choices=['json', 'tools'], default='json')
parser.add_argument('--reset-prefix', action='store_true')
args = parser.parse_args()
sys.path.insert(0, str(args.source.resolve()))

from labagent.config import Settings
from labagent.main import create_app
from labagent.models import Evidence, NewSession, ReadingCandidate
from labagent.providers import Gateway

args.output.mkdir(parents=True, exist_ok=True)

class MeasuredGateway(Gateway):
    def __init__(self, settings):
        super().__init__(settings)
        self.requests = []

    def request(self, base, key, path, **kwargs):
        if args.reset_prefix and path == 'chat/completions':
            # Disable reuse on the measured request itself. An unrelated request
            # does not flush llama.cpp's RAM-backed prompt cache.
            kwargs['json']['cache_prompt'] = False
        start = time.perf_counter()
        result = super().request(base, key, path, **kwargs)
        payload = kwargs.get('json', {})
        self.requests.append({
            'seconds': time.perf_counter()-start,
            'usage': result.get('usage'), 'timings': result.get('timings'),
            'finish_reason': result.get('choices', [{}])[0].get('finish_reason'),
            'prompt_chars': sum(len(m.get('content', '')) for m in payload.get('messages', [])),
            'max_tokens': payload.get('max_tokens'),
            'cache_prompt': payload.get('cache_prompt', 'server_default'),
        })
        return result

settings = Settings(data_dir=(args.output/'isolated-data').resolve(), chat_provider='api',
                    llm_base_url='http://127.0.0.1:8081/v1', llm_model='qwen3-4b-local',
                    http_timeout=240, evidence_ttl_seconds=900)
settings.agent_decision_format = args.format
gateway = MeasuredGateway(settings)
app = create_app(settings, gateway)
store, controller = app.state.store, app.state.task_agent
plan = json.loads((args.source/'configs/battery_plan.json').read_text())
plan['measurement']['samples'] = 3
report = {'variant': args.variant, 'real_llm': True, 'synthetic_states': True,
          'production_records_modified': False, 'cases': []}
if args.resume and (args.output/'benchmark.json').is_file():
    report = json.loads((args.output/'benchmark.json').read_text())

def save():
    (args.output/'benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))

cases = [
    ('equipment_unobserved', 'equipment', 'inspect_equipment'),
    ('equipment_seen_needs_human', 'equipment', 'ask_user'),
    ('ready_to_measure', 'measurement', 'read_display'),
    ('second_measurement_frame', 'measurement', 'read_display'),
    ('step_complete', 'meter_setup', 'advance'),
    ('save_measurements', 'save', 'save_measurement'),
    ('report_ready', 'report', 'report'),
]
try:
    for name, gate, expected in cases:
        if args.cases and name not in args.cases:
            continue
        if any(c['case']==name for c in report['cases']):
            continue
        state=store.create(NewSession(mode='replay',automation='agent',title='SYNTHETIC TIMING FIXTURE'),plan)
        def prepare(s):
            s.step=next(i for i,item in enumerate(s.plan['steps']) if item['gate']==gate)
            for item in s.plan['steps'][:s.step]:
                if item['gate'] in {'read_sop','meter_setup','manual_check'}:
                    s.checks[item['id']]=Evidence(value=True,source='replay',reference='SYNTHETIC BENCHMARK')
            if gate!='equipment':
                s.facts['objects']=Evidence(value=plan['required_objects'],source='replay')
                s.facts['meter_range']=Evidence(value='dc_20v',source='replay')
            if name=='equipment_seen_needs_human':
                s.facts['observed_objects']=Evidence(value={k:True for k in plan['required_objects']},source='replay')
            if name=='second_measurement_frame':
                s.candidates.append(ReadingCandidate(value=1.5,raw_text='1.5',meter_range='dc_20v',
                    step_id=s.plan['steps'][s.step]['id'],reference='image:'+hashlib.sha256(b'synthetic').hexdigest()))
            if name=='step_complete':
                s.checks[s.plan['steps'][s.step]['id']]=Evidence(value=True,source='replay')
            if gate in {'save','report'}:
                from labagent.models import Measurement, now
                s.measurements=[Measurement(value=1.5,at=now(),source='replay',confidence=1,
                    saved=gate=='report',reference='SYNTHETIC BENCHMARK') for _ in range(3)]
            return {'accepted':True,'synthetic':True}
        state,_=store.mutate(state.id,'test.synthetic_context',{},prepare)
        count=len(gateway.requests)
        start=time.perf_counter()
        decision=controller.decide(state.id,'benchmark-'+name,state.revision)
        valid=decision['model_used'] and decision['tool']==expected
        if name=='equipment_unobserved':
            valid=valid and decision['arguments']=={'mode':'fast'}
        entry={'case':name,'seconds':time.perf_counter()-start,'expected':expected,'passed':valid,
               'decision':decision,'request':gateway.requests[-1] if len(gateway.requests)>count else None}
        if args.reset_prefix:
            entry['cold_cache_verified'] = (entry['request'].get('timings') or {}).get('cache_n') == 0
        report['cases'].append(entry);save()
        print(name,round(entry['seconds'],3),decision['tool'],valid,flush=True)
    report['contract_passed']=all(c['passed'] for c in report['cases'])
    if args.reset_prefix:
        report['cold_cache_verified']=all(c.get('cold_cache_verified') for c in report['cases'])
finally:
    gateway.close();save()
