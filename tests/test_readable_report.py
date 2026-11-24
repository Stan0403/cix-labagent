from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import pytest
from PIL import Image

from labagent.config import ROOT
from labagent.models import Evidence, Measurement, ReadingCandidate, State
from labagent.plans import Plan
from labagent.report import html_report, markdown_report
from labagent.report_data import Table, build_report

START = datetime(2026, 11, 24, 4, 0, tzinfo=timezone.utc)


class Visible(HTMLParser):
    def __init__(self, value):
        super().__init__()
        self.words = []
        self.headings = []
        self.images = []
        self.level = None
        self.skip = False
        self.feed(value)

    def handle_starttag(self, tag, attrs):
        if tag == "style":
            self.skip = True
        if tag == "h2":
            self.level = tag
        if tag == "img":
            self.images.append(dict(attrs))

    def handle_endtag(self, tag):
        if tag == "style":
            self.skip = False
        if tag == "h2":
            self.level = None

    def handle_data(self, data):
        if not self.skip:
            self.words.append(data)
        if self.level:
            self.headings.append(data)


@pytest.fixture
def report_state(tmp_path):
    plan = Plan.model_validate_json(
        (ROOT / "configs/battery_plan.json").read_text(encoding="utf-8")
    ).model_dump()
    state = State(title=plan["title"], plan=plan, created_at=START.isoformat())
    return state, plan


def reading(state, value=1.52, **extra):
    item = Measurement(
        value=value,
        unit="V",
        source="manual",
        confidence=1,
        at=(START + timedelta(minutes=2)).isoformat(),
        **extra,
    )
    state.measurements.append(item)
    return item


def test_report_has_exactly_eight_sections_without_raw_internal_values(report_state):
    state, plan = report_state
    state.facts["private_model_result"] = Evidence(value={"raw_text": "MODEL_RAW_SENTINEL"}, source="vision")
    reading(state, saved=True)
    html = html_report(state, plan, [])
    parsed = Visible(html)
    assert len(parsed.headings) == 8
    assert parsed.headings[1] == "2. 实验目的与依据"
    assert parsed.headings[-1] == "8. 实验结论与完成情况"
    for forbidden in ["MODEL_RAW_SENTINEL", state.id, "raw_text", "SHA-256", "操作审计", "附件"]:
        assert forbidden not in html
    assert "2026-11-24 12:00:00" in html and "1.52 V" in html
    purpose = build_report(state, plan, []).sections[1].blocks
    assert len(purpose) == 2 and sum(len(p) for p in purpose) < 200


def test_saved_unsaved_and_replaced_are_separate_in_statistics(report_state):
    state, plan = report_state
    reading(state, 1.52, saved=True)
    reading(state, 8.88, saved=False)
    reading(state, 9.99, saved=True, superseded=True)
    document = build_report(state, plan, [])
    table = next(b for b in document.sections[5].blocks if isinstance(b, Table))
    assert table.rows == [["V", "1", "1.52", "1.52", "1.52", "—"]]
    html = html_report(state, plan, [])
    assert "8.88 V" in html and "9.99 V" in html and "尚未保存" in html and "已被替代" in html
    assert "一个有效样本" in html


def test_different_units_are_not_averaged_together(report_state):
    state, plan = report_state
    reading(state, 1, saved=True)
    state.measurements.append(
        Measurement(value=1000, unit="mV", source="manual", confidence=1, at=START.isoformat(), saved=True)
    )
    analysis = build_report(state, plan, []).sections[5]
    table = next(b for b in analysis.blocks if isinstance(b, Table))
    assert len(table.rows) == 2
    assert "500.5" not in str(table.rows)
    assert any("分别统计" in b for b in analysis.blocks if isinstance(b, str))


def test_reviewed_sampling_target_is_used_in_purpose_and_actual_history(report_state):
    state, plan = report_state
    state.plan["measurement"]["samples"] = 3
    event = {
        "at": START.isoformat(),
        "kind": "agent.plan_updated",
        "payload": {"input": {"samples": 3}, "result": {"accepted": True}, "step": 0},
    }
    document = build_report(state, plan, [event])
    assert "3 个有效样本" in document.sections[1].blocks[0]
    assert "采样目标调整为 3 次" in str(document.sections[3].blocks)


def test_human_correction_explains_original_and_adopted_without_raw_model_answer(report_state):
    state, plan = report_state
    candidate = ReadingCandidate(
        value=1.53,
        raw_text='{"reading":"1.53","debug":"PRIVATE"}',
        reference="image:" + "a" * 64,
        step_id="measure",
        meter_range="dc_20v",
    )
    state.candidates.append(candidate)
    measurement = Measurement(
        value=1.52,
        unit="V",
        source="vision_confirmed",
        confidence=1,
        at=START.isoformat(),
        saved=True,
        original_value=1.53,
        candidate_id=candidate.id,
        reference=candidate.reference,
    )
    state.measurements.append(measurement)
    content = markdown_report(state, plan, [])
    assert "1.53 V" in content and "1.52 V" in content and "修正说明未记录" in content
    for forbidden in ["PRIVATE", candidate.id, candidate.reference, "vision_confirmed"]:
        assert forbidden not in content


def test_range_is_taken_from_the_measurement_time_not_a_later_setting(report_state):
    state, plan = report_state
    state.facts["meter_range"] = Evidence(
        value="dc_2v", source="manual", at=(START + timedelta(minutes=3)).isoformat()
    )
    reading(state, 1.52, saved=True)
    event = {
        "at": START.isoformat(),
        "kind": "user.action",
        "payload": {
            "input": {"kind": "confirm_step", "meter_range": "dc_20v"},
            "result": {"accepted": True},
            "step": 2,
        },
    }
    table = build_report(state, plan, [event]).sections[4].blocks[0]
    assert table.rows[0][3] == "直流 20 V"


def test_finish_time_is_not_overwritten_by_a_later_rejected_request(report_state):
    state, plan = report_state
    state.status = "completed"
    state.step = len(plan["steps"]) - 1
    state.updated_at = (START + timedelta(hours=5)).isoformat()
    reading(state, saved=True)
    events = [
        {
            "at": (START + timedelta(minutes=4)).isoformat(),
            "kind": "agent.execute",
            "payload": {"input": {"tool": "report"}, "result": {"accepted": True}, "step": state.step},
        },
        {
            "at": state.updated_at,
            "kind": "user.action",
            "payload": {"input": {"kind": "advance"}, "result": {"accepted": False}, "step": state.step},
        },
    ]
    info = build_report(state, plan, events).sections[0].blocks[1]
    assert ["结束时间", "2026-11-24 12:04:00"] in info.rows
    assert ["会话历时（含暂停）", "4分0秒"] in info.rows


def test_incomplete_experiment_never_turns_plan_text_into_completed_operations(report_state):
    state, plan = report_state
    plan["steps"][0]["instruction"] = "THIS_PLAN_INSTRUCTION_IS_NOT_A_COMPLETED_ACTION"
    html = html_report(state, plan, [])
    assert "THIS_PLAN_INSTRUCTION" not in html
    assert "未留存可整理的逐步执行记录" in html and "尚无有效已保存测量" in html
    assert "待完成步骤" in html and "本次计划流程已完成" not in html


def test_complete_flag_with_missing_samples_does_not_claim_target_achieved(report_state):
    state, plan = report_state
    state.status = "completed"
    state.step = len(plan["steps"]) - 1
    plan["measurement"]["samples"] = 3
    reading(state, saved=True)
    html = html_report(state, plan, [])
    assert "不能认定采样任务已完成" in html


def test_images_are_embedded_in_one_file_and_missing_files_do_not_break_export(report_state, tmp_path):
    state, plan = report_state
    reference = "image:" + "a" * 64
    candidate = ReadingCandidate(
        value=1.52, reference=reference, roi=[0.2, 0.1, 0.6, 0.7], meter_range="dc_20v"
    )
    state.candidates.append(candidate)
    state.measurements.append(
        Measurement(
            value=1.52,
            source="vision_confirmed",
            confidence=1,
            at=START.isoformat(),
            saved=True,
            candidate_id=candidate.id,
            reference=reference,
        )
    )
    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGB", (200, 100), "white").save(images / ("a" * 64 + ".jpg"))
    result = Visible(html_report(state, plan, [], image_dir=images))
    assert result.images and all(i["src"].startswith("data:image/") for i in result.images)
    assert not any(i["src"].startswith(("http", "file:")) for i in result.images)
    (images / ("a" * 64 + ".jpg")).unlink()
    assert "该画面未能载入" in html_report(state, plan, [], image_dir=images)


def test_report_escapes_untrusted_text_and_does_not_request_external_assets(report_state):
    state, plan = report_state
    state.title = "<script>alert(1)</script>"
    plan["objective"] = "<img src=x onerror=alert(2)>"
    html = html_report(state, plan, [])
    assert "<script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html and "https://" not in html


@pytest.mark.parametrize("variant", ["valid", "same_frame", "too_soon", "different_range", "missing_support"])
def test_two_frame_statement_requires_the_actual_supporting_records(report_state, variant):
    state, plan = report_state
    a = ReadingCandidate(
        value=1.51, at=START.isoformat(), reference="image:" + "a" * 64, meter_range="dc_20v"
    )
    b = ReadingCandidate(
        value=1.52,
        at=(START + timedelta(seconds=2)).isoformat(),
        reference="image:" + "b" * 64,
        meter_range="dc_20v",
    )
    if variant == "same_frame":
        b.reference = a.reference
    if variant == "too_soon":
        b.at = (START + timedelta(milliseconds=200)).isoformat()
    if variant == "different_range":
        b.meter_range = "dc_2v"
    state.candidates.extend([a, b])
    state.measurements.append(
        Measurement(
            value=1.52,
            source="vision_workflow",
            confidence=0,
            at=b.at,
            saved=True,
            reference=b.reference,
            candidate_id=b.id,
            supporting_candidate_ids=[a.id, b.id] if variant != "missing_support" else [],
        )
    )
    result = html_report(state, plan, [])
    assert ("共同支持一个测量样本" in result) == (variant == "valid")


def test_replay_is_prominently_labeled_and_not_called_real_measurement(report_state):
    state, plan = report_state
    state.mode = "replay"
    reading(state, saved=True)
    result = html_report(state, plan, [])
    assert "非真实测量" in result and "软件演练数据" in result and "真实实验会话记录" not in result


def test_html_and_text_export_are_read_only_and_keep_other_downloads(client):
    state = client.post("/api/sessions", json={"plan_id": "battery-voltage-15v"}).json()
    prefix = f"/api/sessions/{state['id']}"
    before = client.get(prefix + "/events").json()
    response = client.get(prefix + "/export?format=html")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert len(Visible(response.text).headings) == 8 and "附件" not in response.text
    assert client.get(prefix + "/export?format=md").status_code == 200
    assert client.get(prefix + "/export?format=csv").status_code == 200
    assert client.get(prefix + "/export?format=json").status_code == 200
    assert client.get(prefix).json()["revision"] == state["revision"]
    assert client.get(prefix + "/events").json() == before
