"""Full page checks on isolated data and explicit model fixtures; not model accuracy."""
import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import uvicorn  # noqa: E402

from labagent.config import Settings  # noqa: E402
from labagent.main import create_app  # noqa: E402
from labagent.models import Evidence, NewSession  # noqa: E402
from scripts import browser_board_smoke as browser  # noqa: E402


def port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


def run(output, executable=None):
    output.mkdir(parents=True, exist_ok=True)
    browser.OUT = output
    counts, calls, report = {}, [], {'fixture_models': True, 'isolated_data': True, 'cases': []}
    controls = {'hold_meter': True}
    chat_gate,chat_started=threading.Event(),threading.Event()
    chat_gate.set()
    with tempfile.TemporaryDirectory(prefix='labagent-task-qa-') as directory:
        settings = Settings(data_dir=Path(directory)/'data', evidence_ttl_seconds=900,
                            chat_provider='api',
                            llm_base_url='http://127.0.0.1:1/v1', llm_model='explicit-test-fixture',
                            agent_max_actions=50)
        app = create_app(settings)

        def fixture(base, key, route, **kwargs):
            calls.append(route)
            counts[route] = counts.get(route, 0) + 1
            if route == 'chat/completions':
                if '\n问题：' in kwargs['json']['messages'][-1]['content']:
                    chat_started.set()
                    if controls.get('hold_chat'):chat_gate.wait(timeout=12)
                    return {'choices':[{'message':{'content':'电池提供直流电压，因此选择直流档。'},'finish_reason':'stop'}]}
                context = json.loads(kwargs['json']['messages'][-1]['content'])
                available = [entry['name'] for entry in context['tool_catalog']]
                chosen = next(name for name in ('advance', 'report', 'inspect_equipment', 'inspect_step',
                                                'read_display', 'save_measurement', 'ask_user') if name in available)
                arguments = {'mode': 'fast'} if chosen == 'inspect_equipment' else {'message': '请按本步要求操作'} if chosen == 'ask_user' else {}
                message = {'tool': chosen, 'arguments': arguments, 'reason': 'EXPLICIT BROWSER FIXTURE'}
                return {'choices': [{'message': {'content': json.dumps(message)}}]}
            if route in {'inspect', 'inspect_semantic'}:
                labels = json.loads(kwargs['data']['labels'])
                return {'present': {key: True for key in labels}, 'boxes': [], 'backend': 'fixture-not-a-real-model'}
            if route == 'check_step':
                rule = kwargs['data']['rule']
                counts[rule] = counts.get(rule, 0) + 1
                keys = {'meter_setup': ['black_in_com', 'red_in_voltage', 'dc_voltage_mode'],
                        'battery_contact': ['red_on_positive', 'black_on_negative', 'no_short'],
                        'battery_cleanup': ['probes_removed', 'meter_off']}[rule]
                checks = dict.fromkeys(keys, True)
                if counts[rule] == 1 or (rule == 'meter_setup' and controls['hold_meter']):
                    checks[keys[-1]] = None
                return {'checks': checks, 'meter_range': 'dc_20v', 'raw_text': 'EXPLICIT FIXTURE'}
            if route == 'locate_display':
                return {'roi': [.1, .1, .8, .8]}
            if route == 'recognize':
                return {'text': json.dumps({'reading': controls.get('reading', '1.34'), 'unit': 'V'}), 'backend': 'fixture-not-real-ocr'}
            raise AssertionError('Unexpected fixture route: '+route)

        app.state.agent.gateway.request = fixture
        server_port, debug = port(), port()
        base = f'http://127.0.0.1:{server_port}'
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=server_port, log_level='error'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        def request(path):
            with urllib.request.urlopen(base+path, timeout=10) as response:
                return json.load(response)

        for _ in range(80):
            try:
                request('/api/health')
                break
            except OSError:
                time.sleep(.1)
        with (output/'chromium.log').open('wb') as log:
            process = subprocess.Popen([executable or shutil.which('chromium'), '--headless', '--disable-gpu', '--no-sandbox',
                '--no-first-run', '--disable-background-networking', '--disable-dev-shm-usage',
                '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
                '--remote-debugging-address=127.0.0.1', f'--remote-debugging-port={debug}',
                f'--user-data-dir={directory}/chrome', '--window-size=1440,1100', 'about:blank'], stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            c = None
            try:
                info = None
                for _ in range(80):
                    try:
                        new = urllib.request.Request(f'http://127.0.0.1:{debug}/json/new?{base}', method='PUT')
                        with urllib.request.urlopen(new, timeout=1) as response:
                            info = json.load(response)
                        break
                    except OSError:
                        time.sleep(.2)
                assert info, 'Chromium did not start'
                c = browser.CDP(info['webSocketDebuggerUrl'])
                c.call('Runtime.enable')
                c.call('Network.enable')
                c.call('Page.bringToFront')
                c.wait("typeof S!=='undefined'&&S.plan?.id&&!document.getElementById('start-plan').disabled")
                assert c.evaluate("document.querySelectorAll('#human-confirm,#approve-plan').length") == 0
                c.click('#start-plan')
                c.wait("S.session?.step===1&&S.session?.facts.equipment_stability?.value.count===1", timeout=35)
                assert c.evaluate("document.getElementById('plan-setup').classList.contains('hidden')")
                session_id = c.evaluate('S.session.id')
                first = request('/api/sessions/'+session_id)
                assert first['missing_objects'], 'One frame cannot confirm equipment'
                c.screenshot('equipment-verifying.png')
                c.wait("S.session?.step===2&&document.querySelector('.check-unknown')!==null", timeout=35)
                assert request('/api/sessions/'+session_id)['step'] == 2
                c.click('#pause')
                c.wait("S.session.status==='paused'&&document.getElementById('primary-action').dataset.action==='resume'")
                c.evaluate('window.taskTestOldDocument=true')
                c.call('Page.reload')
                c.wait("typeof S!=='undefined'&&!window.taskTestOldDocument&&S.session?.status==='paused'&&document.getElementById('task-phase').textContent==='已暂停'")
                assert c.evaluate("document.getElementById('plan-setup').classList.contains('hidden')")
                c.screenshot('paused-after-refresh.png')
                controls['hold_meter'] = False
                c.click('#primary-action')
                c.wait("S.session?.status==='completed'", timeout=100)
                finished = request('/api/sessions/'+session_id)
                assert finished['measurements'][0]['saved']
                assert len(finished['measurements'][0]['supporting_candidate_ids']) == 2
                assert finished['facts']['objects']['source'] == 'vision'
                assert len(finished['facts']['equipment_stability']['value']['frames']) == 2
                c.screenshot('completed-desktop.png')
                report['cases'].append({'case': 'agent_full_flow_pause_refresh_resume', 'passed': True,
                    'normal_start_clicks': 1, 'deliberate_pause_resume_clicks': 2,
                    'measurements': len(finished['measurements'])})

                # An explicitly manual step must still require its actual range.
                plan = app.state.store.get(session_id).plan
                manual = app.state.store.create(NewSession(title='MANUAL UI FIXTURE', automation='workflow'), json.loads(json.dumps(plan)))
                def prepare(state):
                    state.step = 2
                    state.plan['steps'][2]['visual_check'] = 'manual'
                    state.checks['read'] = Evidence(value=True, source='manual')
                    state.facts['objects'] = Evidence(value=state.plan['required_objects'], source='manual')
                    return {'accepted': True}
                manual, _ = app.state.store.mutate(manual.id, 'test.fixture', {}, prepare)
                c.evaluate(f"selectSession({json.dumps(manual.id)},{{fresh:true}})")
                c.wait("document.getElementById('primary-action').dataset.action==='confirm_step'")
                assert c.evaluate("!document.getElementById('meter-panel').classList.contains('hidden')")
                c.screenshot('manual-one-action.png')
                c.evaluate("document.getElementById('meter-range').value='dc_20v';document.getElementById('meter-range').dispatchEvent(new Event('change',{bubbles:true}))")
                c.click('#primary-action')
                c.wait('S.session.step>=3')
                actual = request('/api/sessions/'+manual.id)
                assert actual['facts']['meter_range']['value'] == 'dc_20v'
                report['cases'].append({'case': 'manual_range_confirm_continues_without_second_click', 'passed': True})

                # A plan without an automatic measurement adapter still exposes
                # its actual input, even with automatic guidance enabled.
                custom = json.loads(json.dumps(plan))
                custom['measurement'].update(adapter='manual', unit='°C', min_value=0, max_value=100)
                manual_read = app.state.store.create(NewSession(title='MANUAL ADAPTER FIXTURE'),custom)
                def prepare_reading(state):
                    state.step = next(i for i, item in enumerate(state.plan['steps']) if item['gate']=='measurement')
                    for item in state.plan['steps'][:state.step]:
                        if item['gate'] in {'read_sop','manual_check','meter_setup'}:
                            state.checks[item['id']] = Evidence(value=True,source='manual')
                    state.facts['objects'] = Evidence(value=state.plan['required_objects'],source='manual')
                    return {'accepted':True}
                app.state.store.mutate(manual_read.id,'test.fixture',{},prepare_reading)
                c.evaluate(f"selectSession({json.dumps(manual_read.id)},{{fresh:true}})")
                c.wait("document.getElementById('primary-action').dataset.action==='manual_measurement'")
                assert c.evaluate("!document.getElementById('measurement-panel').classList.contains('hidden')")
                c.evaluate("document.getElementById('manual-value').value='23.4'")
                c.click('#primary-action')
                c.wait("S.session.steps[S.session.step].gate!=='measurement'")
                saved_manual = request('/api/sessions/'+manual_read.id)['measurements'][0]
                assert saved_manual['value']==23.4 and saved_manual['source']=='manual'
                report['cases'].append({'case':'manual_adapter_input_visible_in_auto_mode','passed':True})

                # Permission denial must produce a visible, recoverable main action.
                denied = app.state.store.create(NewSession(title='CAMERA FAILURE FIXTURE', automation='agent'), json.loads(json.dumps(plan)))
                def equipment_state(state):
                    state.step = 1
                    state.checks['read'] = Evidence(value=True, source='manual')
                    return {'accepted': True}
                app.state.store.mutate(denied.id, 'test.fixture', {}, equipment_state)
                c.evaluate("cancelAgentRequest();stopMedia();window.savedGetUserMedia=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=async()=>{throw new DOMException('FIXTURE denied','NotAllowedError')}")
                c.evaluate(f"selectSession({json.dumps(denied.id)},{{fresh:true}})")
                c.wait("document.getElementById('task-card').dataset.phase==='error'", timeout=30)
                assert c.evaluate("document.getElementById('primary-action').dataset.action") == 'retry'
                assert request('/api/sessions/'+denied.id)['step'] == 1
                c.screenshot('camera-error-action.png')
                c.evaluate('navigator.mediaDevices.getUserMedia=window.savedGetUserMedia')
                c.click('#primary-action')
                c.wait('S.session.step>=2', timeout=45)
                report['cases'].append({'case': 'camera_denial_visible_then_retry_recovers', 'passed': True})

                # Opening a new experiment pauses the previous one, including
                # requests whose model response arrives after the switch.
                controls['hold_meter'] = True
                previous = app.state.store.create(NewSession(automation='agent'), json.loads(json.dumps(plan)))
                app.state.store.mutate(previous.id, 'test.fixture', {}, equipment_state)
                c.evaluate(f"selectSession({json.dumps(previous.id)},{{fresh:true}})")
                c.wait(f"S.session?.id==={json.dumps(previous.id)}&&S.session.status==='running'&&!S.switching")
                c.click('#show-setup')
                c.click('#start-plan')
                c.wait(f"S.session?.id!=={json.dumps(previous.id)}&&S.session?.status==='running'&&!S.starting", timeout=30)
                current_id = c.evaluate('S.session.id')
                assert request('/api/sessions/'+previous.id)['status'] == 'paused'
                assert request('/api/sessions/'+manual_read.id)['measurements'][0] == saved_manual
                report['cases'].append({'case': 'new_experiment_pauses_old_and_keeps_history', 'passed': True})

                c.call('Page.reload')
                c.wait(f"typeof S!=='undefined'&&S.session?.id==={json.dumps(current_id)}&&S.session.status==='paused'&&!S.switching")
                refreshed = request('/api/sessions/'+current_id)
                settled = dict(counts)
                time.sleep(1.5)
                assert request('/api/sessions/'+current_id)['revision'] == refreshed['revision']
                assert counts == settled, 'Refresh must not silently restart model requests'
                c.evaluate(f"selectSession({json.dumps(previous.id)})")
                c.wait(f"S.session?.id==={json.dumps(previous.id)}&&S.session.status==='paused'&&!S.switching")
                assert c.evaluate("document.getElementById('primary-action').dataset.action") == 'resume'
                c.screenshot('history-paused.png')
                report['cases'].append({'case': 'refresh_and_history_wait_for_explicit_resume', 'passed': True})

                # Confirmation cancellation leaves every measurement intact.
                c.evaluate("window.confirm=(message)=>{window.deletePrompt=message;return false}")
                c.click('[data-delete-session="'+manual_read.id+'"]')
                c.wait("Boolean(window.deletePrompt)")
                assert request('/api/sessions/'+manual_read.id)['measurements'][0] == saved_manual
                assert c.evaluate("window.deletePrompt.includes('无法从历史中恢复')")
                c.evaluate('window.confirm=()=>true')
                c.click('[data-delete-session="'+manual_read.id+'"]')
                c.wait(f"!document.querySelector('[data-delete-session=\"{manual_read.id}\"]')&&!S.deletingSession")
                assert all(s['id'] != manual_read.id for s in request('/api/sessions'))
                assert c.evaluate('S.session.id') == previous.id
                c.wait(f"!document.querySelector('[data-delete-session=\"{previous.id}\"]').disabled")
                c.click('[data-delete-session="'+previous.id+'"]')
                c.wait("S.session===null&&!document.getElementById('empty').classList.contains('hidden')&&!S.deletingSession")
                assert request('/api/sessions/'+current_id)['status'] == 'paused'
                c.screenshot('deleted-current-empty.png')
                c.evaluate(f"selectSession({json.dumps(current_id)})")
                c.wait(f"S.session?.id==={json.dumps(current_id)}&&!S.switching")
                report['cases'].append({'case': 'delete_confirm_cancel_keep_other_and_clear_current', 'passed': True})

                # The polarity gate now belongs to readout. Fixtures provide
                # negative/zero/positive strings; this checks behavior, not OCR accuracy.
                controls['reading'] = '-1.34'
                c.evaluate("""cancelAgentRequest();stopMedia();navigator.mediaDevices.getUserMedia=async()=>{
                 const canvas=document.createElement('canvas');canvas.width=640;canvas.height=480;
                 const context=canvas.getContext('2d');context.fillStyle='#397b71';context.fillRect(0,0,640,480);
                 const stream=canvas.captureStream(0),track=stream.getVideoTracks()[0];
                 const timer=setInterval(()=>{context.fillRect(0,0,640,480);track.requestFrame();},150);
                 track.addEventListener('ended',()=>clearInterval(timer));return stream;};""")
                polarity = app.state.store.create(NewSession(automation='agent'), json.loads(json.dumps(plan)))
                def polarity_state(state):
                    state.step = next(i for i, item in enumerate(state.plan['steps']) if item['gate']=='measurement')
                    for item in state.plan['steps'][:state.step]:
                        if item['gate'] in {'read_sop','meter_setup'}:
                            state.checks[item['id']] = Evidence(value=True, source='manual')
                    state.facts['objects'] = Evidence(value=state.plan['required_objects'], source='manual')
                    state.facts['meter_range'] = Evidence(value='dc_20v', source='manual')
                    return {'accepted':True}
                app.state.store.mutate(polarity.id,'test.fixture',{},polarity_state)
                c.evaluate(f"selectSession({json.dumps(polarity.id)},{{fresh:true}})")
                c.wait("S.session?.candidates?.some(c=>c.value<0&&c.status==='rejected')",timeout=35)
                assert not request('/api/sessions/'+polarity.id)['measurements']
                assert c.evaluate("document.getElementById('risk').textContent.includes('负')")
                c.screenshot('polarity-negative-desktop.png')
                c.call('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                assert c.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                c.screenshot('polarity-negative-mobile.png')
                controls['reading'] = '0.00'
                c.wait("S.session?.candidates?.some(c=>c.value===0&&c.status==='rejected')",timeout=35)
                assert not request('/api/sessions/'+polarity.id)['measurements']
                controls['reading'] = '1.34'
                c.wait("S.session?.interaction?.phase==='notice'",timeout=55)
                notice_started=time.monotonic()
                decision_count=counts.get('chat/completions',0)
                assert c.evaluate("document.getElementById('task-message').textContent.includes('关闭万用表')")
                assert c.evaluate("document.getElementById('primary-action').classList.contains('hidden')")
                c.screenshot('cleanup-five-second-notice.png')
                time.sleep(.6)
                assert request('/api/sessions/'+polarity.id)['status']=='running'
                assert counts.get('chat/completions',0)==decision_count,'No LLM during reminder'
                c.wait("S.session?.status==='completed'",timeout=65)
                polarity_done = request('/api/sessions/'+polarity.id)
                assert len(polarity_done['measurements'])==1 and polarity_done['measurements'][0]['saved']
                assert len(polarity_done['measurements'][0]['supporting_candidate_ids'])==2
                supporting=[c for c in polarity_done['candidates'] if c['id'] in polarity_done['measurements'][0]['supporting_candidate_ids']]
                assert len({c['reference'] for c in supporting})==1,'Camera fixture pixels must stay identical'
                assert all(c['id'].startswith('capture_') for c in supporting)
                assert not counts.get('battery_contact',0), 'No battery contact VLM calls are allowed'
                assert not counts.get('battery_cleanup',0), 'No cleanup VLM calls are allowed'
                c.screenshot('polarity-positive-completed.png')
                report['cases'].append({'case':'negative_zero_rejected_positive_stable_auto_saved','passed':True})
                report['cases'].append({'case':'static_identical_frames_then_five_second_notice_without_cleanup_model','passed':True,
                                        'static_video_fixture':True,'seconds_after_notice_observed':round(time.monotonic()-notice_started,2)})

                model_count=counts.get('chat/completions',0)
                immediate=c.evaluate("""(()=>{document.getElementById('chat-input').value='现在多少伏？';sendChat();
                 return document.getElementById('messages').textContent.includes('现在多少伏？')&&
                        document.getElementById('messages').textContent.includes('正在读取实验记录');})()""")
                assert immediate,'Question and reply placeholder must render before HTTP completion'
                c.wait("!S.busy&&document.getElementById('messages').textContent.includes('1.34 V')")
                assert counts.get('chat/completions',0)==model_count,'Factual reading must skip LLM'
                assert c.evaluate("[...document.querySelectorAll('#messages .user')].filter(n=>n.textContent.includes('现在多少伏？')).length===1")
                report['cases'].append({'case':'reading_chat_appears_immediately_without_model','passed':True})

                controls['hold_chat']=True;chat_gate.clear();chat_started.clear()
                c.evaluate("document.getElementById('chat-input').value='为什么测电池要用直流电压档？';void sendChat();true")
                assert chat_started.wait(timeout=5),'Chat fixture was not reached'
                assert c.evaluate("document.getElementById('messages').textContent.includes('为什么测电池要用直流电压档？')&&document.getElementById('send-message').disabled")
                c.screenshot('chat-pending-immediate.png')
                chat_gate.set()
                c.wait("!S.busy&&document.getElementById('messages').textContent.includes('因此选择直流档')")
                assert c.evaluate("document.querySelectorAll('#messages [data-chat-pending]').length===0")
                report['cases'].append({'case':'slow_chat_keeps_question_visible_and_reconciles_reply','passed':True})

                chat_gate.clear();chat_started.clear()
                c.evaluate("document.getElementById('chat-input').value='请解释电压和电池容量的关系';void sendChat();true")
                assert chat_started.wait(timeout=5)
                c.evaluate(f"selectSession({json.dumps(current_id)})")
                c.wait(f"S.session?.id==={json.dumps(current_id)}&&!S.switching&&!S.busy")
                chat_gate.set();controls['hold_chat']=False
                assert c.evaluate("!document.getElementById('send-message').disabled")
                assert not c.evaluate("document.getElementById('messages').textContent.includes('电压和电池容量的关系')")
                report['cases'].append({'case':'switch_during_chat_does_not_block_or_contaminate_new_session','passed':True})

                c.call('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
                c.evaluate('window.scrollTo(0,0)')
                assert c.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                assert c.evaluate("document.getElementById('task-card').getBoundingClientRect().top<innerHeight*.8")
                c.screenshot('mobile-task.png')
                report.update(passed=True, javascript_errors=c.errors, tool_counts=counts,
                              production_records_modified=False, mobile_overflow=False)
                assert not c.errors, c.errors
            except Exception:
                if c:
                    try:
                        c.screenshot('failure.png')
                        report['diagnostic'] = c.evaluate("({step:S.session?.step,state:S.session?.status,phase:document.getElementById('task-phase').textContent,message:document.getElementById('task-message').textContent,observation:document.getElementById('observation-status').textContent,toast:document.getElementById('toast').textContent,busy:S.agentRunBusy,starting:S.starting,automatic:S.autoEnabled})")
                        report['javascript_errors'] = c.errors
                        report['tool_counts'] = counts
                    except Exception:
                        pass
                raise
            finally:
                chat_gate.set()
                (output/'browser-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                server.should_exit = True
                thread.join(timeout=10)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser')
    args = parser.parse_args()
    run(args.output, args.browser)
