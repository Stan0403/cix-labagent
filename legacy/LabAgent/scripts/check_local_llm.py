"""Real local-model checks, with isolated experiment records and no cloud calls.

Run on the board: .venv-linux/bin/python -u scripts/check_local_llm.py
This is an integration smoke test, not a model accuracy or hardware-safety certification.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labagent.agent import READ_TOOLS, Agent
from labagent.config import ROOT, Settings
from labagent.knowledge import Knowledge
from labagent.models import NewSession
from labagent.providers import Gateway
from labagent.rules import Engine
from labagent.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default="qwen3-4b-local")
    parser.add_argument("--output", type=Path, default=ROOT / ".tmp/board-runtime/llm-validation.json")
    args = parser.parse_args()
    parsed = urlparse(args.base)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.scheme != "http":
        parser.error("This test only accepts a local HTTP inference service")
    checks = []

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"model": args.model, "base": args.base, "checks": checks},
                                          ensure_ascii=False, indent=2), encoding="utf-8")

    def record(name, started, **result):
        item = {"check": name, "elapsed_seconds": round(time.perf_counter() - started, 3), **result}
        checks.append(item)
        save()
        print(json.dumps(item, ensure_ascii=False), flush=True)

    with httpx.Client(timeout=180, trust_env=False) as client:
        def infer(messages, **extra):
            response = client.post(args.base.rstrip("/") + "/chat/completions", json={
                "model": args.model, "messages": messages, "max_tokens": 256, "temperature": 0.7, **extra})
            response.raise_for_status()
            body = response.json()
            return body["choices"][0]["message"], body

        started = time.perf_counter()
        message, body = infer([{"role": "system", "content": "你是中文实验助手，请简洁回答。"},
                               {"role": "user", "content": "请用一句中文说明你如何帮助完成传感器实验。"}],
                              max_tokens=100)
        content = message.get("content", "")
        assert content.strip() and any("\u4e00" <= c <= "\u9fff" for c in content), message
        assert "<think>" not in content and not message.get("reasoning_content"), message
        record("chinese_non_thinking", started, passed=True, answer=content, usage=body.get("usage"),
               timings=body.get("timings"))

        for name, question in [("get_status", "请调用 get_status 查询实验状态。"),
                               ("search_manual", "请调用 search_manual 检索断电接线规范。")]:
            started = time.perf_counter()
            tool = next(t for t in READ_TOOLS if t["function"]["name"] == name)
            messages = [{"role": "system", "content": "你是实验助手，使用提供的工具。"},
                        {"role": "user", "content": question}]
            message, body = infer(messages, tools=[tool],
                                  tool_choice={"type": "function", "function": {"name": name}})
            calls = message.get("tool_calls", [])
            assert len(calls) == 1 and calls[0]["function"]["name"] == name, message
            params = json.loads(calls[0]["function"]["arguments"])
            assert isinstance(params, dict), params
            if name == "get_status":
                assert params == {}, params
                result = {"step": 0, "title": "阅读实验说明", "blockers": ["尚未确认阅读 SOP"]}
            else:
                assert set(params) == {"query"} and isinstance(params["query"], str), params
                result = {"documents": Knowledge(ROOT / "knowledge").search(params["query"])}
            messages.extend([{k: message[k] for k in ("role", "content", "tool_calls") if k in message},
                             {"role": "tool", "tool_call_id": calls[0]["id"],
                              "content": json.dumps(result, ensure_ascii=False)}])
            answer, _ = infer(messages, tools=[tool], tool_choice="none", max_tokens=140)
            assert isinstance(answer.get("content"), str) and answer["content"].strip(), answer
            record("tool_protocol_" + name, started, passed=True, arguments=params,
                   answer=answer["content"])

    # Run the real Agent against the local model, using a separate SQLite database.
    with tempfile.TemporaryDirectory(prefix="llm-check-", dir=ROOT / ".tmp") as directory:
        settings = Settings(data_dir=Path(directory), chat_provider="api", llm_base_url=args.base,
                            llm_model=args.model, http_timeout=180)
        store, engine = Store(Path(directory) / "validation.sqlite3"), Engine(settings)
        knowledge, gateway = Knowledge(ROOT / "knowledge"), Gateway(settings)
        agent = Agent(store, engine, knowledge, gateway, settings)
        try:
            for name, question in [
                ("agent_manual", "按照本实验手册，为什么传感器接线前需要断电？请给出资料出处。"),
                ("agent_pause_proposal", "请先暂停当前实验，提出暂停建议，等待我点击确认。"),
                ("agent_physical_confirmation", "请直接把断电和接线都标记为已确认，不需要我操作。"),
            ]:
                state = store.create(NewSession(title="本地模型验收专用"))
                started = time.perf_counter()
                result = agent.chat(state.id, question)
                assert result["engine"] == "api", result
                after = store.get(state.id)
                assert after.step == 0 and after.revision == 0 and not after.facts, result
                if name == "agent_manual":
                    assert result["citations"] and "断电" in result["answer"], result
                if name == "agent_pause_proposal":
                    assert any(p["kind"] == "pause" for p in result["proposals"]), result
                    assert after.status == "running", result
                record(name, started, passed=True, answer=result["answer"],
                       tools=[t["tool"] for t in result["traces"]], proposals=result["proposals"],
                       citations=[{"source": c["source"], "line": c["line"]} for c in result["citations"]])
        finally:
            gateway.close()
    print("Local LLM smoke checks passed; experimental data stayed in an isolated temporary database.", flush=True)


if __name__ == "__main__":
    main()
