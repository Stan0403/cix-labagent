"""Real local LLM validation, with an isolated DB and explicitly synthetic states."""
# ruff: noqa: E402

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from labagent.config import Settings
from labagent.main import create_app
from labagent.models import Evidence, NewSession
from labagent.planning import generate_plan
from labagent.providers import Gateway


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--llm-base", default="http://127.0.0.1:8081/v1")
    parser.add_argument("--llm-model", default="qwen3-4b-local")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        data_dir=(args.output / "isolated-data").resolve(),
        chat_provider="api",
        llm_base_url=args.llm_base,
        llm_model=args.llm_model,
        http_timeout=180,
    )
    gateway = Gateway(settings)
    report = {
        "real_llm": True,
        "synthetic_state": True,
        "physical_measurements_performed": False,
        "cases": [],
    }
    try:
        start = time.perf_counter()
        plan = generate_plan("测量单节1.5V干电池端电压，测量3次并生成报告。", "", "goal.txt", gateway, "llm")
        report["planning_seconds"] = time.perf_counter() - start
        report["plan"] = plan.model_dump()
        (args.output / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        print(
            "PLAN",
            plan.title,
            plan.measurement,
            plan.questions,
            round(report["planning_seconds"], 2),
            flush=True,
        )
        app = create_app(settings, gateway)
        for name in ("equipment_unobserved", "equipment_seen_needs_another_frame", "ready_to_measure"):
            state = app.state.store.create(
                NewSession(mode="replay", automation="agent", title="MODEL VALIDATION FIXTURE"),
                plan.model_dump(),
            )

            def prepare(s):
                measurement = next(
                    i for i, item in enumerate(s.plan["steps"]) if item["gate"] == "measurement"
                )
                s.step = (
                    measurement
                    if name == "ready_to_measure"
                    else next(i for i, item in enumerate(s.plan["steps"]) if item["gate"] == "equipment")
                )
                for item in s.plan["steps"][: s.step]:
                    if item["gate"] in {"read_sop", "meter_setup", "manual_check"}:
                        s.checks[item["id"]] = Evidence(
                            value=True, source="replay", reference="EXPLICIT_SYNTHETIC_TEST_STATE"
                        )
                if name == "ready_to_measure":
                    s.facts["objects"] = Evidence(value=s.plan["required_objects"], source="replay")
                    s.facts["meter_range"] = Evidence(value="dc_20v", source="replay")
                if name == "equipment_seen_needs_another_frame":
                    s.facts["observed_objects"] = Evidence(
                        value={k: True for k in s.plan["required_objects"]}, source="replay"
                    )
                return {"accepted": True, "synthetic": True}

            state, _ = app.state.store.mutate(
                state.id, "test.synthetic_context", {"synthetic": True}, prepare
            )
            start = time.perf_counter()
            decision = app.state.task_agent.decide(state.id, "model-contract-" + name, state.revision)
            entry = {"case": name, "seconds": time.perf_counter() - start, "decision": decision}
            report["cases"].append(entry)
            (args.output / "model-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                "DECISION",
                name,
                decision["tool"],
                decision["arguments"],
                round(entry["seconds"], 2),
                flush=True,
            )
        expected = {
            "equipment_unobserved": "inspect_equipment",
            "equipment_seen_needs_another_frame": "inspect_equipment",
            "ready_to_measure": "read_display",
        }
        report["contract_passed"] = not plan.questions and all(
            case["decision"]["model_used"] and case["decision"]["tool"] == expected[case["case"]]
            for case in report["cases"]
        )
        if not report["contract_passed"]:
            raise RuntimeError("Real model decisions did not meet the three-case preflight contract")
    finally:
        gateway.close()
        (args.output / "model-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
