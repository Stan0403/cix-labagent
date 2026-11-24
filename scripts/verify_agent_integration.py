"""Real LLM + real detector, using a stored image and isolated replay records."""
# ruff: noqa: E402

import argparse
import io
import json
import sys
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from labagent.config import Settings
from labagent.main import create_app
from labagent.models import Evidence, NewSession
from labagent.plans import Plan
from labagent.providers import Gateway


def document():
    text = """实验名称：单节干电池端电压重复测量
实验器材：单节1.5V干电池、数字万用表、红表笔、黑表笔。
实验步骤：
1. 黑表笔接COM，红表笔接V/Ω，选择直流20V档。
2. 红表笔接电池正极，黑表笔接负极，保持接触稳定。
3. 独立采集3次端电压，保存读数，计算平均值和极差。
4. 移开表笔并关闭万用表，生成报告。
仅用于低压干电池实验。"""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + "".join("<w:p><w:r><w:t>" + escape(line) + "</w:t></w:r></w:p>" for line in text.splitlines())
            + "</w:body></w:document>",
        )
    return out.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        data_dir=(args.output / "isolated-data").resolve(),
        chat_provider="api",
        llm_base_url="http://127.0.0.1:8081/v1",
        llm_model="qwen3-4b-local",
        ocr_base_url="http://127.0.0.1:8031",
        http_timeout=180,
    )
    app = create_app(settings, Gateway(settings))
    report = {
        "real_llm": True,
        "real_detector": True,
        "synthetic_state": True,
        "image_source": "stored camera capture",
        "physical_task_performed": False,
    }
    try:
        with TestClient(app) as client:
            start = time.perf_counter()
            response = client.post(
                "/api/plans/import",
                data={"planner": "agent", "method": "llm"},
                files={
                    "file": (
                        "battery-test.docx",
                        document(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
            response.raise_for_status()
            uploaded = response.json()
            assert uploaded["input_mode"] == "document" and not uploaded["approved"]
            assert uploaded["measurement"]["samples"] == 3 and not uploaded["questions"], uploaded
            report["document_plan"] = {
                "seconds": time.perf_counter() - start,
                "plan_id": uploaded["id"],
                "samples": uploaded["measurement"]["samples"],
                "source_filename": uploaded["source_filename"],
            }
            print("REAL_DOCUMENT_PLAN", json.dumps(report["document_plan"]), flush=True)
            plan = Plan.model_validate_json(args.plan.read_text(encoding="utf-8"))
            state = app.state.store.create(NewSession(mode="replay", automation="agent"), plan.model_dump())

            def fixture(s):
                s.step = next(i for i, item in enumerate(s.plan["steps"]) if item["gate"] == "equipment")
                for item in s.plan["steps"][: s.step]:
                    s.checks[item["id"]] = Evidence(
                        value=True, source="replay", reference="EXPLICIT_SYNTHETIC_TEST_STATE"
                    )
                return {"accepted": True, "synthetic": True}

            state, _ = app.state.store.mutate(state.id, "test.fixture", {"synthetic": True}, fixture)
            decision = app.state.task_agent.decide(state.id, "integration-equipment-test", state.revision)
            assert decision["tool"] == "inspect_equipment" and decision["arguments"]["mode"] == "fast", (
                decision
            )
            start = time.perf_counter()
            result = app.state.task_agent.execute(
                state.id,
                decision["id"],
                app.state.perform_observation,
                UploadFile(file=io.BytesIO(args.image.read_bytes()), filename="stored-test.jpg"),
            )
            assert result["backend"] == "yolo26n-cix-npu" and all(result["present"].values()), result
            assert len(result["boxes"]) >= 4, result
            state = app.state.store.get(state.id)
            assert app.state.engine.blockers(state), "Observation must not substitute human confirmation"
            report["equipment"] = {
                "backend": result["backend"],
                "present": result["present"],
                "boxes": result["boxes"],
                "http_tool_seconds": time.perf_counter() - start,
                "human_confirmation_still_required": True,
            }
            print("REAL_EQUIPMENT_TOOL", json.dumps(report["equipment"]), flush=True)
            follow = app.state.task_agent.decide(state.id, "integration-followup-test", state.revision)
            assert follow["tool"] == "ask_user", follow
            report["followup"] = follow
            report["passed"] = True
            print("REAL_FOLLOWUP", follow["tool"], flush=True)
    finally:
        (args.output / "integration-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
