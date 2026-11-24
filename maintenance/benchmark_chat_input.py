"""Real Qwen input/first-token measurements using a read-only experiment snapshot."""

import argparse
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--agent-file", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--variant", choices=["before", "after"], required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.source))
from labagent.config import Settings
from labagent.knowledge import Knowledge
from labagent.models import State, Evidence
from labagent.providers import Gateway
from labagent.rules import Engine
from labagent.task_agent import TaskAgent

FORMAL = Path("/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo")
settings = Settings.load()
# Candidate files have no .env. Only the existing service configuration supplies
# actual model addresses; the model and hardware are unchanged by this benchmark.
settings.llm_base_url = "http://127.0.0.1:8081/v1"
settings.llm_model = "qwen3-4b-local"
settings.http_timeout = 240
args.output.mkdir(parents=True, exist_ok=True)
snapshot = args.output / "snapshot.json"
dbpath = FORMAL / "data/labagent.sqlite3"
if not snapshot.exists():
    with sqlite3.connect("file:" + str(dbpath) + "?mode=ro", uri=True) as db:
        raw = db.execute(
            "SELECT state FROM sessions ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()[0]
        sid = json.loads(raw)["id"]
        rows = db.execute(
            "SELECT role,content FROM messages WHERE session_id=? ORDER BY sequence DESC LIMIT 12",
            (sid,),
        ).fetchall()[::-1]
    snapshot.write_text(
        json.dumps(
            {
                "state": json.loads(raw),
                "history": [{"role": r, "content": c} for r, c in rows],
            },
            ensure_ascii=False,
        )
    )
data = json.loads(snapshot.read_text())
state = State.model_validate(data["state"])


class Mirror:
    @contextmanager
    def connect(self):
        with sqlite3.connect("file:" + str(dbpath) + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            yield db

    def get(self, sid):
        return state.model_copy(deep=True)

    def audit(self, *a, **kw):
        pass


class Capture(Gateway):
    def request(self, base, key, path, **kwargs):
        self.payload = kwargs["json"]
        return {
            "choices": [
                {"message": {"content": "CAPTURE ONLY"}, "finish_reason": "stop"}
            ]
        }


engine = Engine(settings)
store = Mirror()
gateway = Capture(settings)
spec = importlib.util.spec_from_file_location(
    "labagent.benchmark_dialogue", args.agent_file
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
agent = module.Agent(
    store, engine, Knowledge(FORMAL / "data/knowledge"), gateway, settings
)
controller = object.__new__(TaskAgent)
controller.settings, controller.store, controller.engine = settings, store, engine
controller._guard = threading.RLock()
controller._preview = {}
controller._busy = set()
agent.task_agent = controller
question = "为什么测电池要用直流电压档？"
agent._model_chat(state.id, question, data["history"])
payload = gateway.payload
(args.output / (args.variant + "-payload.json")).write_text(
    json.dumps(payload, ensure_ascii=False, indent=2)
)
report = {
    "variant": args.variant,
    "real_model": True,
    "snapshot_read_only": True,
    "production_experiments_modified": False,
    "output_token_limit": 32,
    "cases": [],
}


def measure(name, body, cache):
    body = {
        **body,
        "max_tokens": 32,
        "stream": True,
        "stream_options": {"include_usage": True},
        "timings_per_token": True,
        "cache_prompt": cache,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    start = time.perf_counter()
    first = None
    first_text = None
    usage = None
    timings = None
    parts = []
    finish = None
    with httpx.Client(trust_env=False, timeout=240) as client:
        with client.stream(
            "POST", "http://127.0.0.1:8081/v1/chat/completions", json=body
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                text = line[5:].strip()
                if text == "[DONE]":
                    break
                item = json.loads(text)
                usage = item.get("usage") or usage
                timings = item.get("timings") or timings
                for choice in item.get("choices", []):
                    delta = choice.get("delta") or {}
                    if first is None and (
                        delta.get("content")
                        or delta.get("tool_calls")
                        or delta.get("reasoning_content")
                    ):
                        first = time.perf_counter() - start
                    if delta.get("content"):
                        if first_text is None:
                            first_text = time.perf_counter() - start
                        parts.append(delta["content"])
                    finish = choice.get("finish_reason") or finish
    record = {
        "case": name,
        "cache_prompt": cache,
        "first_delta_seconds": round(first, 3) if first else None,
        "first_text_seconds": round(first_text, 3) if first_text else None,
        "total_seconds": round(time.perf_counter() - start, 3),
        "prompt_chars": sum(len(m.get("content") or "") for m in body["messages"]),
        "tool_count": len(body.get("tools", [])),
        "usage": usage,
        "timings": timings,
        "finish_reason": finish,
    }
    report["cases"].append(record)
    (args.output / (args.variant + "-benchmark.json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(record, ensure_ascii=False), flush=True)


measure("same_question_cold", payload, False)
if args.variant == "after":
    measure("same_chat_cached", payload, True)
    test = state.model_copy(deep=True)
    test.id = "chat-benchmark-decision"
    test.status = "running"
    test.step = next(
        i for i, s in enumerate(test.plan["steps"]) if s["gate"] == "measurement"
    )
    test.candidates = []
    test.measurements = []
    test.facts["objects"] = Evidence(
        value=test.plan["required_objects"], source="manual"
    )
    for step in test.plan["steps"][: test.step]:
        if step["gate"] in {"meter_setup", "read_sop"}:
            test.checks[step["id"]] = Evidence(value=True, source="manual")
    allowed = controller.available(test)
    messages, options = controller._decision_request(
        test, allowed, controller._context(test, allowed)
    )
    gateway.chat(messages, decision=True, **options)
    measure("interleaved_agent_decision", gateway.payload, True)
    measure("chat_after_agent_decision", payload, True)
gateway.close()
