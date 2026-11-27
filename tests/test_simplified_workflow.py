import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from labagent.models import Action, Evidence, Measurement


def create(client):
    return client.post('/api/sessions', json={'plan_id': 'battery-voltage-15v'}).json()


def action(client, state, kind, **extra):
    r = client.post(f"/api/sessions/{state['id']}/actions", json={
        'kind': kind, 'expected_revision': state['revision'], **extra})
    assert r.status_code == 200, r.text
    return r.json()


def equipment(client):
    result = action(client, create(client), 'confirm_step', confirmed=True, advance_after=True)
    assert result['advanced'] and result['session']['step'] == 1
    return result['session']


def image(variant):
    out = io.BytesIO()
    Image.new('RGB', (128, 96), (variant, 0, 0)).save(out, format='PNG')
    return out.getvalue()


def observe(client, state, variant, automatic=True):
    result = client.post(f"/api/sessions/{state['id']}/observe", data={
        'task': 'objects', 'automatic': str(automatic).lower()},
        files={'file': ('frame.png', image(variant), 'image/png')})
    assert result.status_code == 200, result.text
    return result.json()


@pytest.fixture
def camera(client, monkeypatch):
    import labagent.demo_api as module
    frames = {'present': {'battery': True, 'multimeter': True, 'probes': True}, 'boxes': []}
    monkeypatch.setattr(client.app.state.agent.gateway, 'request', lambda *a, **k: frames)
    clock = {'at': datetime.now(timezone.utc) - timedelta(seconds=10)}
    monkeypatch.setattr(module, 'now', lambda: clock['at'].isoformat())
    return frames, clock


def test_confirm_and_continue_is_one_revision_and_still_checks_inventory(client):
    s = create(client)
    response = action(client, s, 'confirm_step', confirmed=True, advance_after=True)
    s = response['session']
    assert s['revision'] == 1 and s['step'] == 1 and s['checks']['read']['source'] == 'manual'
    partial = action(client, s, 'confirm_equipment', confirmed=True, advance_after=True, objects=['battery'])
    assert partial['accepted'] and not partial.get('advanced') and partial['session']['step'] == 1
    complete = action(client, partial['session'], 'confirm_equipment', confirmed=True,
                      advance_after=True, objects=['battery', 'multimeter', 'probes'])
    assert complete['advanced'] and complete['session']['step'] == 2


def test_two_independent_equipment_frames_are_automatic_evidence(client, camera):
    frames, clock = camera
    s = equipment(client)
    one = observe(client, s, 1)
    assert not one['equipment_verified'] and one['session']['missing_objects']
    assert one['session']['agent_decision']['tool'] == 'inspect_equipment'
    clock['at'] += timedelta(seconds=2)
    two = observe(client, one['session'], 2)
    assert two['equipment_verified'] and two['session']['missing_objects'] == []
    assert two['session']['facts']['objects']['source'] == 'vision'
    proof = two['session']['facts']['equipment_stability']['value']
    assert proof['stable'] and len({f['reference'] for f in proof['frames']}) == 2
    assert two['session']['agent_decision']['tool'] == 'advance'
    actual = client.app.state.store.get(s['id'])
    actual.automation = 'agent'
    assert 'advance' in client.app.state.task_agent.available(actual)


@pytest.mark.parametrize('second_result', ['complete', 'missing', 'same_image', 'paused'])
def test_agent_equipment_verification_uses_one_model_decision(client, camera, monkeypatch, second_result):
    frames, clock = camera
    app = client.app
    app.state.agent.settings.llm_base_url = 'http://127.0.0.1:8081/v1'
    app.state.agent.settings.llm_model = 'test'
    s = client.post('/api/sessions', json={
        'plan_id': 'battery-voltage-15v', 'automation': 'agent'}).json()
    s = action(client, s, 'confirm_step', confirmed=True, advance_after=True)['session']
    calls = []

    def choose(*args, **kwargs):
        calls.append(1)
        return {'content': json.dumps({
            'tool': 'inspect_equipment', 'arguments': {'mode': 'fast'}, 'reason': '清点器材'})}

    monkeypatch.setattr(app.state.agent.gateway, 'chat', choose)

    def decide(state, request):
        r = client.post(f"/api/sessions/{s['id']}/agent/decide", json={
            'expected_revision': state['revision'], 'request_id': request})
        assert r.status_code == 200, r.text
        return r.json()['decision']

    def execute(decision, variant):
        r = client.post(f"/api/sessions/{s['id']}/agent/execute",
                        data={'decision_id': decision['id']},
                        files={'file': ('frame.png', image(variant), 'image/png')})
        assert r.status_code == 200, r.text
        return r.json()

    first = decide(s, 'equipment-first')
    assert first['model_used']
    one = execute(first, 1)
    assert not one['equipment_verified'] and one['session']['step'] == 1
    second = decide(one['session'], 'equipment-second')
    assert second['tool'] == 'inspect_equipment' and not second['model_used']
    assert decide(one['session'], 'equipment-second-retry')['id'] == second['id']
    clock['at'] += timedelta(seconds=2)
    if second_result == 'missing':
        frames['present']['battery'] = False
    if second_result == 'paused':
        perform = app.state.perform_observation

        def pause_after_observation(*args, **kwargs):
            result = perform(*args, **kwargs)
            app.state.store.mutate(s['id'], 'test.pause', {}, lambda state:
                app.state.engine.apply(state, Action(kind='pause')))
            return result

        monkeypatch.setattr(app.state, 'perform_observation', pause_after_observation)
    two = execute(second, 1 if second_result == 'same_image' else 2)
    assert len(calls) == 1
    if second_result == 'complete':
        assert two['equipment_verified'] and two['advanced']
        assert two['session']['step'] == 2
        again = execute(second, 2)
        assert again['session']['revision'] == two['session']['revision']
        assert again['session']['step'] == 2
    else:
        assert not two.get('advanced') and two['session']['step'] == 1
        assert two['session']['status'] == ('paused' if second_result == 'paused' else 'running')
    if second_result == 'missing':
        import time
        waiting = two['session']
        assert waiting['interaction']['waiting_for_equipment']
        path = f"/api/sessions/{s['id']}"

        def preview():
            r = client.post(path + '/equipment-preview', files={'file': ('p.png', image(4), 'image/png')})
            assert r.status_code == 200, r.text
            return r.json()['inventory_ready']

        def still_waiting():
            r = client.post(path + '/agent/decide', json={
                'expected_revision': waiting['revision'], 'request_id': 'unchanged-scene'})
            assert r.status_code == 409

        for _ in range(3):
            assert not preview()
            still_waiting()
        frames['present']['battery'] = True
        assert not preview()
        still_waiting()
        frames['present']['battery'] = None
        assert not preview()
        frames['present']['battery'] = True
        assert not preview()
        time.sleep(.8)
        still_waiting()  # A single frame cannot become stable merely by aging.
        assert preview()
        resumed = decide(waiting, 'inventory-returned')
        assert not resumed['model_used']
        first_back = execute(resumed, 4)
        assert not first_back['equipment_verified']
        clock['at'] += timedelta(seconds=2)
        final = execute(decide(first_back['session'], 'inventory-verify'), 5)
        assert final['advanced'] and final['session']['step'] == 2
        assert len(calls) == 1


@pytest.mark.parametrize('failure', ['same_image', 'too_soon', 'unknown', 'missing', 'expired', 'manual_only'])
def test_equipment_cannot_auto_pass_invalid_evidence(client, camera, failure):
    frames, clock = camera
    s = equipment(client)
    one = observe(client, s, 1, automatic=failure != 'manual_only')
    clock['at'] += timedelta(seconds=2 if failure != 'too_soon' else .2)
    if failure == 'unknown':
        frames['present']['probes'] = None
    if failure == 'missing':
        frames['present'].pop('probes')
    if failure == 'expired':
        clock['at'] += timedelta(seconds=100)
    two = observe(client, one['session'], 1 if failure == 'same_image' else 2,
                  automatic=failure != 'manual_only')
    assert not two['equipment_verified'] and two['session']['missing_objects']


def test_unknown_frame_resets_positive_equipment_history(client, camera):
    frames, clock = camera
    s = equipment(client)
    one = observe(client, s, 1)
    frames['present']['probes'] = None
    clock['at'] += timedelta(seconds=2)
    observe(client, one['session'], 2)
    frames['present']['probes'] = True
    clock['at'] += timedelta(seconds=2)
    last = observe(client, one['session'], 3)
    assert not last['equipment_verified']
    assert last['session']['facts']['equipment_stability']['value']['count'] == 1


@pytest.mark.parametrize('mode',['live','replay'])
def test_fresh_negative_invalidates_prior_automatic_equipment(client, camera, mode):
    frames,clock=camera
    s=client.post('/api/sessions',json={'plan_id':'battery-voltage-15v','mode':mode}).json()
    s=action(client,s,'confirm_step',confirmed=True,advance_after=True)['session']
    observe(client,s,1)
    clock['at']+=timedelta(seconds=2)
    assert observe(client,s,2)['equipment_verified']
    frames['present']['battery']=False
    clock['at']+=timedelta(seconds=2)
    last=observe(client,s,3,automatic=False)['session']
    assert last['missing_objects'] and 'objects' not in last['facts']


def test_forged_vision_object_fact_without_stability_proof_is_not_accepted(client):
    s = equipment(client)
    store = client.app.state.store
    store.mutate(s['id'], 'test.unverified', {}, lambda state: state.facts.update(objects=Evidence(
        value=state.plan['required_objects'], source='vision', reference='image:'+'a'*64)))
    current = client.get(f"/api/sessions/{s['id']}").json()
    assert current['missing_objects']


def test_equipment_stability_expires_and_never_bypasses_meter(client, camera):
    _, clock = camera
    s = equipment(client)
    observe(client, s, 1)
    clock['at'] += timedelta(seconds=2)
    s = observe(client, s, 2)['session']
    s = action(client, s, 'advance')['session']
    assert s['step'] == 2 and s['blockers']
    denied = action(client, s, 'confirm_step', confirmed=True, advance_after=True)
    assert not denied['accepted'] and denied['session']['step'] == 2
    store = client.app.state.store
    def expire(state):
        state.facts['objects'].at = (datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    store.mutate(s['id'], 'test.expire', {}, expire)
    assert client.get(f"/api/sessions/{s['id']}").json()['missing_objects']


def test_confirmation_does_not_continue_without_explicit_confirmation(client):
    s = create(client)
    result = action(client, s, 'confirm_step', advance_after=True)
    assert not result['accepted'] and result['session']['step'] == 0
    invalid = client.post(f"/api/sessions/{s['id']}/actions", json={
        'kind': 'advance', 'advance_after': True})
    assert invalid.status_code == 422


def test_task_status_survives_refresh_pause_and_manual_fallback(client):
    s = equipment(client)
    paused = action(client, s, 'pause')['session']
    assert paused['interaction']['phase'] == 'paused'
    assert paused['interaction']['primary_action']['kind'] == 'resume'
    refreshed = client.get(f"/api/sessions/{s['id']}").json()
    assert refreshed['interaction'] == paused['interaction']
    s = action(client, refreshed, 'resume', confirmed=True)['session']
    s = action(client, s, 'confirm_equipment', confirmed=True, advance_after=True,
               objects=s['plan']['required_objects'])['session']
    store = client.app.state.store
    store.mutate(s['id'], 'test.manual_step', {}, lambda state: state.plan['steps'][state.step].update(visual_check='manual'))
    view = client.get(f"/api/sessions/{s['id']}").json()
    assert view['interaction']['phase'] == 'waiting_user'
    assert view['interaction']['primary_action']['kind'] == 'confirm_step'


def test_saving_existing_verified_measurement_does_not_invent_a_new_setup_confirmation(client):
    s=create(client)
    def saved_stage(state):
        state.step=next(i for i, item in enumerate(state.plan['steps']) if item['gate']=='save')
        state.measurements.append(Measurement(value=1.5,source='manual',confidence=1,
            at=datetime.now(timezone.utc).isoformat()))
        return {'accepted':True}
    client.app.state.store.mutate(s['id'],'test.fixture',{},saved_stage)
    current=client.get(f"/api/sessions/{s['id']}").json()
    assert current['agent_decision']['tool']=='save_measurement'
    assert current['interaction']['phase']=='saving'
