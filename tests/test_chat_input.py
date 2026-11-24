"""Dialogue reads the same evidence as the workflow and forwards only relevant facts."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from labagent.chat_context import SYSTEM, compact_documents
from labagent.models import Evidence, Measurement, NewSession, ReadingCandidate
from labagent.providers import Gateway


def prepare(client, readings=(1.34,), automation="agent"):
    plan = client.app.state.plans.get("battery-voltage-15v").model_dump()
    store = client.app.state.store
    state = store.create(NewSession(automation=automation), plan)

    def ready(s):
        s.step = next(i for i, item in enumerate(plan["steps"]) if item["gate"] == "measurement")
        s.facts["objects"] = Evidence(value=plan["required_objects"], source="manual")
        s.facts["meter_range"] = Evidence(value="dc_20v", source="manual")
        for item in plan["steps"][: s.step]:
            if item["gate"] in {"read_sop", "meter_setup"}:
                s.checks[item["id"]] = Evidence(value=True, source="manual")
        for i, value in enumerate(readings):
            s.candidates.append(
                ReadingCandidate(
                    value=value,
                    step_id=plan["steps"][s.step]["id"],
                    at=(datetime.now(timezone.utc) - timedelta(seconds=4 - i * 2)).isoformat(),
                    reason="小数点不清" if value is None else "",
                    reference="image:" + str(i) * 64,
                    meter_range="dc_20v",
                )
            )
        return {"accepted": True}

    state, _ = store.mutate(state.id, "test.fixture", {}, ready)
    return state


@pytest.mark.parametrize("question", ["现在多少伏？", "测到多少了？", "刚才读数是多少？"])
@pytest.mark.parametrize("automation", ["agent", "workflow"])
def test_voltage_synonyms_read_pending_candidate_without_model(client, monkeypatch, question, automation):
    state = prepare(client, automation=automation)
    monkeypatch.setattr(
        client.app.state.agent.gateway, "chat", lambda *a, **kw: pytest.fail("No LLM for factual readout")
    )
    lock = client.app.state.agent.lock(state.id)
    lock.acquire()
    try:
        result = client.post(f"/api/sessions/{state.id}/chat", json={"message": question}).json()
    finally:
        lock.release()
    assert result["engine"] == "workflow" and "1.34 V" in result["answer"]
    assert "等待第二次采集确认" in result["answer"] and "尚未记录" in result["answer"]
    assert not result["session"]["measurements"]


def test_latest_failed_capture_explains_why_no_record_was_added(client, monkeypatch):
    state = prepare(client, (1.34, None))
    monkeypatch.setattr(client.app.state.agent.gateway, "chat", lambda *a, **kw: pytest.fail("No LLM"))
    result = client.post(f"/api/sessions/{state.id}/chat", json={"message": "为什么刚才没记录？"}).json()
    assert (
        "1.34 V" in result["answer"] and "小数点不清" in result["answer"] and "尚未新增" in result["answer"]
    )


@pytest.mark.parametrize("saved", [True, False])
def test_confirmed_measurement_and_saved_state_come_from_actual_record(client, saved):
    state = prepare(client, ())

    def measured(s):
        s.measurements.append(
            Measurement(
                value=9.99,
                source="manual",
                confidence=1,
                at=datetime.now(timezone.utc).isoformat(),
                superseded=True,
            )
        )
        s.measurements.append(
            Measurement(
                value=1.35,
                source="manual",
                confidence=1,
                at=datetime.now(timezone.utc).isoformat(),
                saved=saved,
            )
        )

    client.app.state.store.mutate(state.id, "test.record", {}, measured)
    answer = client.post(f"/api/sessions/{state.id}/chat", json={"message": "现在多少伏？"}).json()["answer"]
    assert "1.35 V" in answer and "9.99" not in answer
    assert ("已保存" if saved else "尚未保存") in answer


def test_stale_candidate_is_not_reported_as_current_voltage(client):
    state = prepare(client)
    client.app.state.store.mutate(
        state.id,
        "test.stale",
        {},
        lambda s: setattr(
            s.candidates[-1], "at", (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        ),
    )
    answer = client.post(f"/api/sessions/{state.id}/chat", json={"message": "现在多少伏？"}).json()["answer"]
    assert "1.34" not in answer and "没有当前有效" in answer


def test_hazard_with_readout_question_still_pauses_immediately(client, monkeypatch):
    state = prepare(client)
    monkeypatch.setattr(client.app.state.agent.gateway, "chat", lambda *a, **kw: pytest.fail("No LLM"))
    lock = client.app.state.agent.lock(state.id)
    lock.acquire()
    try:
        result = client.post(
            f"/api/sessions/{state.id}/chat", json={"message": "现在多少伏？电池发热了"}
        ).json()
    finally:
        lock.release()
    assert result["session"]["status"] == "paused"


def test_status_tool_and_recording_prompt_are_bounded_without_raw_payloads(client, monkeypatch):
    state = prepare(client, (1.34, None))

    def noisy(s):
        s.facts["unrelated_image"] = Evidence(
            value={"raw_text": "RAW_SENTINEL" * 5000, "bbox": [0.1, 0.2, 0.3, 0.4]}, source="vision"
        )
        s.candidates[-1].raw_text = "OCR_RAW_SENTINEL" * 1000

    client.app.state.store.mutate(state.id, "test.noisy", {}, noisy)
    agent = client.app.state.agent
    seen = []
    monkeypatch.setattr(
        agent.gateway,
        "chat",
        lambda messages, tools=None, **kw: (
            seen.append((messages, tools, kw)) or {"content": "依据记录回答。"}
        ),
    )
    monkeypatch.setattr(
        agent, "_search", lambda *a: pytest.fail("Recording question must not retrieve manuals")
    )
    agent._model_chat(
        state.id, "为什么刚才没记录？", [{"role": "assistant", "content": "UNRELATED_HISTORY_SENTINEL"}]
    )
    text = json.dumps(seen[0][0], ensure_ascii=False)
    assert seen[0][0][0] == {"role": "system", "content": SYSTEM}
    assert seen[0][1] is None and seen[0][2]["conversation"] is True
    assert len(text) < 1400 and "RAW_SENTINEL" not in text and "HISTORY_SENTINEL" not in text
    assert "1.34" in text and "小数点不清" in text
    status = agent._tool(state.id, "get_status", {}, "", 0)
    assert (
        len(json.dumps(status, ensure_ascii=False)) < 1000 and "facts" not in status and "plan" not in status
    )


def test_principle_only_retrieves_relevant_passages_and_keeps_system_prefix_fixed(client, monkeypatch):
    state = prepare(client)
    agent = client.app.state.agent
    seen = []
    queries = []
    monkeypatch.setattr(
        agent.gateway,
        "chat",
        lambda messages, tools=None, **kw: seen.append(messages) or {"content": "干电池电压为直流。"},
    )

    def search(state, query):
        queries.append(query)
        return [
            {
                "source": "battery.md",
                "line": 1,
                "text": "准备器材。\n黑表笔接COM，红表笔接VΩ，选择直流电压档。\n保持接触稳定。",
            }
        ]

    monkeypatch.setattr(agent, "_search", search)
    agent._model_chat(state.id, "为什么要用直流电压档？", [])
    client.app.state.store.mutate(
        state.id, "test.change", {}, lambda s: setattr(s.candidates[-1], "value", 1.35)
    )
    agent._model_chat(state.id, "为什么测电池要用直流档？", [])
    assert len(queries) == 2 and seen[0][0] == seen[1][0] == {"role": "system", "content": SYSTEM}
    assert "battery.md" in seen[0][1]["content"] and "candidates" not in seen[0][1]["content"]
    assert "1.34" not in seen[0][1]["content"] and "1.35" not in seen[1][1]["content"]


def test_compact_passage_retains_real_source_line_numbers():
    document = {
        "source": "manual.md",
        "line": 10,
        "text": "无关开头\n准备器材\n直流电压档，黑COM红VΩ。\n保持稳定\n无关结尾",
    }
    result = compact_documents([document], "为什么用直流电压档")
    assert result[0]["line"] == 11 and "直流电压档" in result[0]["text"]
    assert len(result[0]["text"]) <= 240


def test_conversation_transport_requests_cache_without_changing_model_or_decision_budget(settings):
    settings.llm_base_url = "http://localhost/v1"
    settings.llm_model = "qwen3-4b-local"
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "回答"}, "finish_reason": "stop"}],
                "timings": {"cache_n": 120, "prompt_n": 50},
                "usage": {"prompt_tokens": 170},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        reply = Gateway(settings, client).chat(
            [{"role": "system", "content": SYSTEM}, {"role": "assistant", "content": None, "tool_calls": []}],
            conversation=True,
        )
    assert seen[0]["model"] == "qwen3-4b-local" and seen[0]["cache_prompt"] is True
    assert seen[0]["max_tokens"] == 256 and seen[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert reply["_performance"]["timings"]["cache_n"] == 120
