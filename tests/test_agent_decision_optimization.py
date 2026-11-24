"""Decision latency changes must preserve model authority and state-bound execution."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from labagent.main import create_app
from labagent.models import Action, DomainError, Evidence, NewSession
from labagent.providers import Gateway, ProviderError


@pytest.fixture
def agent_env(settings):
    settings.llm_base_url = "http://127.0.0.1:8081/v1"
    settings.llm_model = "test-model"
    app = create_app(settings)
    plan = app.state.plans.get("battery-voltage-15v").model_dump()
    state = app.state.store.create(NewSession(mode="replay", automation="agent"), plan)

    def prepare(s):
        s.step = 1
        s.checks[plan["steps"][0]["id"]] = Evidence(value=True, source="replay")
        return {"accepted": True}

    state, _ = app.state.store.mutate(state.id, "test.prepare", {}, prepare)
    yield app, state
    app.state.agent.gateway.close()


def message(tool, arguments=None):
    return {"content": json.dumps({"tool": tool, "arguments": arguments or {}, "reason": "依据现场证据"})}


@pytest.mark.parametrize("format_", ["json", "tools"])
def test_decision_transport_is_short_cached_and_rejects_truncation(settings, format_):
    settings.llm_base_url, settings.llm_model = "http://localhost:8081/v1", "test"
    seen = []
    finish = ["stop", "length"]

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": message("advance"),
            "finish_reason": finish.pop(0)}], "usage": {"prompt_tokens": 50},
            "timings": {"cache_n": 20, "prompt_n": 30}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        gateway = Gateway(settings, client)
        options = {"response_format": {"type": "json_object"}} if format_ == "json" else {"tools": []}
        result = gateway.chat([{"role": "user", "content": "选择行动"}], decision=True, **options)
        assert result["_performance"]["timings"]["cache_n"] == 20
        assert seen[0]["max_tokens"] == 192 and seen[0]["cache_prompt"] is True
        assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False}
        with pytest.raises(ProviderError, match="截断"):
            gateway.chat([], decision=True, **options)


def test_ordinary_chat_budget_is_unchanged(settings):
    settings.llm_base_url, settings.llm_model = "http://localhost/v1", "test"
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "回答"}}]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = Gateway(settings, client).chat([])
    assert seen[0]["max_tokens"] == 256 and "cache_prompt" not in seen[0]
    assert "_performance" not in result


def test_frame_timestamp_does_not_invalidate_prompt_prefix_but_presence_does(agent_env):
    app, state = agent_env
    agent = app.state.task_agent
    state.facts["observed_objects"] = Evidence(value={"battery": True}, source="replay")
    available = agent.available(state)
    agent.record_preview(state.id, {"present": {"battery": True}})
    first = agent._decision_request(state, available, agent._context(state, available))[0]
    agent.record_preview(state.id, {"present": {"battery": True}})
    same = agent._decision_request(state, available, agent._context(state, available))[0]
    assert first == same  # capture timestamps are not model-visible state changes
    state.revision += 1
    revised = agent._decision_request(state, available, agent._context(state, available))[0]
    assert revised[0] == first[0]
    assert revised[1]["content"].rsplit('"revision":', 1)[0] == first[1]["content"].rsplit('"revision":', 1)[0]
    agent.record_preview(state.id, {"present": {"battery": False}})
    changed = agent._decision_request(state, available, agent._context(state, available))[0]
    assert changed != revised


def test_expired_observation_is_not_treated_as_complete(agent_env):
    app, state = agent_env
    agent = app.state.task_agent
    state.facts["observed_objects"] = Evidence(value={k: True for k in state.plan["required_objects"]},
        source="replay", at=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat())
    available = agent.available(state)
    context = agent._context(state, available)
    assert context["equipment_phase"] == "observed_incomplete"
    assert context["observed_equipment"]["fresh"] is False
    assert "inspect_equipment" in available


def test_compaction_preserves_confirmed_equipment_and_current_step_completion(agent_env):
    app, state = agent_env
    agent = app.state.task_agent
    state.step = 2
    state.facts["objects"] = Evidence(value=state.plan["required_objects"], source="replay")
    context = agent._context(state, agent.available(state))
    assert context["confirmed_equipment"] == state.plan["required_objects"]
    assert context["prerequisites_met"] and not context["step_complete"]
    state.checks[state.plan["steps"][state.step]["id"]] = Evidence(value=True, source="replay")
    assert agent._context(state, agent.available(state))["step_complete"]
    state.facts["objects"].at = (datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    expired = agent._context(state, agent.available(state))
    assert not expired["prerequisites_met"] and expired["confirmed_equipment"] == []


def test_missing_samples_are_distinct_from_missing_physical_confirmations(agent_env):
    app, state = agent_env
    state.step = next(i for i, item in enumerate(state.plan["steps"]) if item["gate"] == "measurement")
    for item in state.plan["steps"][:state.step]:
        if item["gate"] in {"read_sop", "meter_setup", "manual_check"}:
            state.checks[item["id"]] = Evidence(value=True, source="replay")
    state.facts["objects"] = Evidence(value=state.plan["required_objects"], source="replay")
    state.facts["meter_range"] = Evidence(value="dc_20v", source="replay")
    agent = app.state.task_agent
    context = agent._context(state, agent.available(state))
    assert context["prerequisites_met"] and not context["step_complete"]
    assert context["measurement_progress"]["recorded"] == 0
    assert "read_display" in agent.available(state)


def test_waiting_human_reuses_decision_but_new_revision_calls_model(agent_env, monkeypatch):
    app, state = agent_env
    agent, store = app.state.task_agent, app.state.store
    calls = []

    def choose(*args, **kwargs):
        calls.append(kwargs)
        return message("ask_user", {"message": "请最终提交器材清单"}) if len(calls) == 1 else message("advance")

    monkeypatch.setattr(app.state.agent.gateway, "chat", choose)
    first = agent.decide(state.id, "request-original", state.revision)
    agent.execute(state.id, first["id"], lambda *a, **kw: pytest.fail("No camera for ask_user"))
    cached = agent.decide(state.id, "request-again", state.revision)
    assert first["id"] == cached["id"] and len(calls) == 1
    state, _ = store.mutate(state.id, "user.confirm", {}, lambda s: app.state.engine.apply(s,
        Action(kind="confirm_equipment", objects=s.plan["required_objects"], confirmed=True)))
    assert agent.decide(state.id, "request-updated", state.revision)["tool"] == "advance"
    assert len(calls) == 2


def test_model_pause_race_discards_decision(agent_env, monkeypatch):
    app, state = agent_env

    def choose(*args, **kwargs):
        app.state.store.mutate(state.id, "user.pause", {}, lambda s: app.state.engine.apply(s, Action(kind="pause")))
        return message("inspect_equipment", {"mode": "fast"})

    monkeypatch.setattr(app.state.agent.gateway, "chat", choose)
    with pytest.raises(DomainError, match="状态已变化"):
        app.state.task_agent.decide(state.id, "pause-race-request", state.revision)
    with app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 0
    assert app.state.store.get(state.id).status == "paused"


def test_prompt_only_offers_authorized_actions_and_rejects_other_tools(agent_env, monkeypatch):
    app, state = agent_env
    agent = app.state.task_agent
    messages, options = agent._decision_request(state, agent.available(state), agent._context(state, agent.available(state)))
    tools = {b["properties"]["tool"]["const"] for b in options["response_format"]["schema"]["oneOf"]}
    assert tools == {"ask_user", "search_manual", "inspect_equipment"}
    assert "tool_catalog" in json.loads(messages[-1]["content"])
    monkeypatch.setattr(app.state.agent.gateway, "chat", lambda *a, **kw: message("advance"))
    with pytest.raises(ProviderError, match="不允许"):
        agent.decide(state.id, "invalid-tool-request", state.revision)
    assert app.state.store.get(state.id).revision == state.revision


def test_performance_is_audit_metadata_and_action_stays_revision_bound(agent_env, monkeypatch):
    app, state = agent_env
    reply = message("inspect_equipment", {"mode": "fast"})
    reply["_performance"] = {"timings": {"cache_n": 100}, "http_seconds": .2}
    monkeypatch.setattr(app.state.agent.gateway, "chat", lambda *a, **kw: reply)
    decision = app.state.task_agent.decide(state.id, "audit-timing-request", state.revision)
    assert decision["model_used"] and "_performance" not in decision
    event = app.state.store.events(state.id)[-1]
    assert event["payload"]["performance"]["timings"]["cache_n"] == 100
    app.state.store.mutate(state.id, "user.pause", {}, lambda s: app.state.engine.apply(s, Action(kind="pause")))
    with pytest.raises(DomainError, match="状态变化"):
        app.state.task_agent.execute(state.id, decision["id"], lambda *a, **kw: pytest.fail("Stale action executed"))
