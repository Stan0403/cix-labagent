"""Readable experiment records derived from saved state, never raw model prose."""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import mean

from .battery_polarity import independent_captures, reading_issue
from .step_checks import RULES

LOCAL = timezone(timedelta(hours=8))
RANGES = {"dc_200mv": "直流 200 mV", "dc_2v": "直流 2 V", "dc_20v": "直流 20 V"}
SOURCES = {
    "manual": "人工确认",
    "vision": "图像核验",
    "vision_confirmed": "图像识别后人工复核",
    "vision_workflow": "连续图像核验",
    "ocr": "图像识别",
    "serial": "设备采集",
    "replay": "软件演练",
}


def text(value, limit=180):
    if not isinstance(value, str):
        return "未记录"
    value = re.sub(r"[\s\x00-\x1f]+", " ", value).strip()
    return (value[:limit].rstrip() + "…" if len(value) > limit else value) or "未记录"


def stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def when(value, short=False):
    parsed = stamp(value)
    return (
        parsed.astimezone(LOCAL).strftime("%H:%M:%S" if short else "%Y-%m-%d %H:%M:%S")
        if parsed
        else "未记录"
    )


def number(value):
    return format(value, ".12g") if value is not None and math.isfinite(value) else "未记录"


def valid_reference(value):
    return isinstance(value, str) and re.fullmatch(r"image:[0-9a-f]{64}", value) is not None


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]


@dataclass
class Figure:
    number: int
    caption: str
    reference: str
    roi: tuple = ()


@dataclass
class Section:
    title: str
    blocks: list = field(default_factory=list)


@dataclass
class Document:
    title: str
    sections: list[Section]
    replay: bool


def event_records(events, step_count):
    """Map acknowledgements to the step before an atomic confirm-and-continue."""
    previous = 0
    rows = []
    for event in events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        inputs, result = payload.get("input", {}), payload.get("result", {})
        inputs = inputs if isinstance(inputs, dict) else {}
        result = result if isinstance(result, dict) else {}
        after = payload.get("step")
        before = previous
        if isinstance(after, int) and 0 <= after < step_count:
            previous = after
        rows.append(
            {
                "at": event.get("at", ""),
                "kind": event.get("kind", ""),
                "input": inputs,
                "result": result,
                "before": before,
                "after": after,
                "action": inputs.get("kind", inputs.get("tool", "")),
                "accepted": result.get("accepted") is True,
            }
        )
    return rows


def build_report(state, spec, events):
    steps = spec["steps"]
    plan = state.plan or {}
    measurement = plan.get("measurement")
    if not state.plan:
        measurement = {"quantity": "电压", "unit": "V", "samples": 1}
    records = event_records(events, len(steps))
    candidates = {c.id: c for c in state.candidates}
    active = [m for m in state.measurements if m.saved and not m.superseded]
    unsaved = [m for m in state.measurements if not m.saved and not m.superseded]
    labels = {m.id: f"测量{i + 1}" for i, m in enumerate(state.measurements)}

    def source(value):
        return "软件演练" if state.mode == "replay" else SOURCES.get(value, "来源未记录")

    sections = [
        Section(title)
        for title in (
            "实验基本信息与结果摘要",
            "实验目的与依据",
            "实验器材与仪表设置",
            "实验过程记录",
            "测量数据与现场图像",
            "数据分析",
            "异常情况与人工修正",
            "实验结论与完成情况",
        )
    ]
    figures, figure_index = [], {}

    def figure(reference, caption, roi=()):
        if not valid_reference(reference):
            return None
        key = (reference, tuple(roi or ()))
        if key in figure_index:
            return figure_index[key]
        if len(figures) >= 12:
            return None
        item = Figure(len(figures) + 1, caption, reference, tuple(roi or ()))
        figures.append(item)
        figure_index[key] = item
        return item

    def figure_name(item):
        return f"图{item.number}" if item else "未留存或未选入正文"

    def evidence_for(rule):
        found = None
        for record in records:
            if record["kind"] == "workflow.visual_check":
                proof = record["input"].get("checks", {})
                if isinstance(proof, dict) and proof.get("rule") == rule:
                    found = (proof, "vision", record["input"].get("reference", ""), record["at"])
        for key, item in state.facts.items():
            if not key.startswith("workflow_check:"):
                continue
            if isinstance(item.value, dict) and item.value.get("rule") == rule:
                found = (item.value, item.source, item.reference, item.at)
        return found

    ranges = []
    for record in records:
        if not record["accepted"]:
            continue
        value = record["input"].get("meter_range")
        proof = record["input"].get("checks", {})
        if record["kind"] == "workflow.visual_check" and isinstance(proof, dict) and proof.get("passed"):
            value = proof.get("meter_range")
        if isinstance(value, str) and value in RANGES:
            ranges.append(
                (record["at"], value, "vision" if record["kind"] == "workflow.visual_check" else "manual")
            )
    current_range = state.facts.get("meter_range")
    if current_range and isinstance(current_range.value, str) and current_range.value in RANGES:
        ranges.append((current_range.at, current_range.value, current_range.source))
    ranges.sort(key=lambda item: stamp(item[0]) or datetime.min.replace(tzinfo=timezone.utc))

    def range_for(m):
        candidate = candidates.get(m.candidate_id)
        if candidate and candidate.meter_range in RANGES:
            return RANGES[candidate.meter_range]
        recorded_at = stamp(m.at)
        prior = [item for item in ranges if recorded_at and stamp(item[0]) and stamp(item[0]) <= recorded_at]
        return RANGES[prior[-1][1]] if prior else "未记录"

    def stability(m):
        supporting = [candidates[c] for c in m.supporting_candidate_ids if c in candidates]
        if len(supporting) != 2:
            return []
        a, b = supporting
        ta, tb = stamp(a.at), stamp(b.at)
        if (
            a.value is None
            or b.value is None
            or not ta
            or not tb
            or abs((tb - ta).total_seconds()) < 1
            or not independent_captures(a, b)
            or not valid_reference(a.reference)
            or not valid_reference(b.reference)
            or not a.meter_range
            or a.meter_range != b.meter_range
            or not math.isclose(a.value, b.value, abs_tol=0.02, rel_tol=0)
        ):
            return []
        return sorted(supporting, key=lambda c: stamp(c.at))

    def method(m):
        if state.mode == "replay":
            return "软件演练数据"
        if m.source == "vision_workflow":
            if any(c.id == m.candidate_id and c.confirmation_method == "single_frame" for c in state.candidates):
                return "单帧显示屏识别"
            return "两帧稳定性核验" if stability(m) else "图像识别（核验帧明细未记录）"
        return {
            "manual": "人工录入",
            "vision_confirmed": "图像识别后人工复核",
            "serial": "设备采集",
            "ocr": "图像识别",
            "replay": "软件演练数据",
        }.get(m.source, "未记录")

    finish = None
    for record in records:
        if record["accepted"] and (
            record["action"] == "report"
            or (record["action"] == "advance" and record["before"] == len(steps) - 1)
        ):
            finish = record["at"]
    status = {"completed": "已完成", "paused": "已暂停", "running": "进行中"}[state.status]
    target = (measurement or {}).get("samples")
    info = [
        ["实验名称", text(state.title)],
        ["数据性质", "软件演练（非真实测量）" if state.mode == "replay" else "真实实验会话记录"],
        ["当前状态", status],
        ["开始时间", when(state.created_at)],
        ["结束时间", when(finish) if finish else "未记录" if state.status == "completed" else "尚未结束"],
        ["有效已保存样本", f"{len(active)} / {target}" if target else "本计划未设置数值采样任务"],
    ]
    if finish and stamp(finish) and stamp(state.created_at):
        seconds = max(0, int((stamp(finish) - stamp(state.created_at)).total_seconds()))
        info.append(["会话历时（含暂停）", f"{seconds // 60}分{seconds % 60}秒"])
    sections[0].blocks += ["时间均为北京时间（UTC+8）。", Table(["项目", "记录"], info)]
    if active:
        reading = active[-1]
        sections[0].blocks.append(
            f"本次已保存 {len(active)} 个有效样本，最近一次结果为 {number(reading.value)} {text(reading.unit)}。"
        )
    elif measurement:
        sections[0].blocks.append("尚无有效已保存测量，不能据此给出最终测量结果。")

    objective = plan.get("objective") or spec.get("objective")
    if measurement:
        brief = f"测量{text(measurement.get('quantity', '实验数值'), 60)}，按本次确认的计划取得 {target} 个有效样本并记录结果。"
    elif objective:
        brief = re.split(r"(?<=[。！？])", text(objective, 120))[0]
    else:
        brief = "按本次确认的计划完成现场操作与检查，记录执行结果。"
    filename = plan.get("source_filename", "")
    filename = re.split(r"[/\\]", filename)[-1] if isinstance(filename, str) else ""
    basis = (
        "操作人员提供的实验目标及本次确认的操作计划"
        if plan.get("input_mode") == "goal"
        else (text(filename, 80) + "及本次确认的操作计划" if filename else "本次采用的实验操作计划")
    )
    sections[1].blocks += ["目的：" + brief, "依据：" + basis + "。"]

    object_labels = plan.get("object_labels") or spec.get("object_labels", {})
    objects = state.facts.get("objects")
    observed = state.facts.get("observed_objects")
    object_items = objects.value if objects and isinstance(objects.value, list) else []
    object_source = objects.source if objects else ""
    object_reference = objects.reference if objects else ""
    historical_objects = False
    if not objects:
        for record in records:
            if record["accepted"] and record["action"] == "confirm_equipment":
                object_items, object_source = record["input"].get("objects", []), "manual"
                historical_objects = True
            elif record["kind"] == "perception.objects" and record["result"].get("equipment_verified"):
                present = record["input"].get("present", {})
                object_items = [key for key, value in present.items() if value is True]
                object_source, object_reference = "vision", record["input"].get("reference", "")
                historical_objects = True
    equipment = Table(["器材", "核对记录", "方式"], [])
    for key, label in object_labels.items():
        confirmed = key in object_items
        seen = observed and isinstance(observed.value, dict) and observed.value.get(key) is True
        equipment.rows.append(
            [
                text(label),
                "曾核对，当前需复查"
                if confirmed and historical_objects
                else "已核对"
                if confirmed
                else "观察到，尚未完成核验"
                if seen
                else "未留存核对记录",
                source(object_source) if confirmed else "—",
            ]
        )
    if equipment.rows:
        sections[2].blocks.append(equipment)
    else:
        sections[2].blocks.append("未记录器材清单。")
    setting_rows = [
        ["最后留存的量程", RANGES[ranges[-1][1]] if ranges else "未记录"],
        ["仪表型号与精度", "未单独记录"],
    ]
    meter = evidence_for("meter_setup")
    if meter:
        for key, label in RULES["meter_setup"]["checks"].items():
            value = meter[0].get("checks", {}).get(key)
            setting_rows.append(
                [
                    label,
                    ("演练核验通过" if state.mode == "replay" else "图像核验通过")
                    if value is True
                    else "未满足"
                    if value is False
                    else "未看清或未记录",
                ]
            )
    elif ranges:
        setting_rows.append(["档位与插孔检查", source(ranges[-1][2]) + "；未留存逐项图像核验结果"])
    sections[2].blocks.append(Table(["仪表设置", "记录"], setting_rows))
    for reference, caption in [
        (object_reference or (observed.reference if observed else ""), "器材现场记录"),
        (meter[2] if meter else "", "仪表档位与插孔核对画面"),
    ]:
        item = figure(reference, caption)
        if item:
            sections[2].blocks.append(item)

    timeline = []
    if state.completion_notice_until:
        sections[3].blocks.append("读数保存后，系统发出移开表笔并关闭万用表的收尾提示；未对收尾操作进行视觉核验。")
    actions = {
        "confirm_step": "人工确认本步操作完成",
        "read_sop": "已阅读并确认实验要求",
        "confirm_meter_setup": "人工确认仪表设置",
        "confirm_equipment": "人工提交器材核对结果",
        "confirm_power_off": "人工确认断电",
        "confirm_connected": "人工确认连接完成",
        "confirm_wiring": "人工复核接线",
        "confirm_power_on": "人工确认接通电源",
        "pause": "暂停实验指导",
        "resume": "恢复实验指导",
        "invalidate_wiring": "现场变化，返回准备阶段重新核对",
        "remeasure": "开始重新测量，原读数保留",
        "save_measurement": "保存测量记录",
    }
    emitted = set()
    for record in records:
        if not record["accepted"]:
            continue
        title = text(steps[record["before"]]["title"])
        description, reference = "", record["input"].get("reference", "")
        if record["action"] in actions:
            description = actions[record["action"]]
            if record["action"] == "confirm_equipment":
                given = record["input"].get("objects", [])
                description += (
                    "（齐全）"
                    if all(k in given for k in spec.get("required_objects", []))
                    else "（仍有器材缺失）"
                )
        elif record["kind"] == "workflow.visual_check" and record["result"].get("passed"):
            description = "图像核验通过"
        elif record["kind"] == "perception.objects" and record["result"].get("equipment_verified"):
            description = "两张独立画面的器材核验通过"
        elif record["kind"] == "agent.plan_updated":
            count = record["input"].get("samples")
            if isinstance(count, int) and 1 <= count <= 10:
                description = f"采样目标调整为 {count} 次，已有记录保留"
        elif record["kind"] == "agent.recheck":
            description = "返回先前步骤重新核对"
        elif record["at"] == finish:
            description = "实验流程结束"
        if description:
            if state.mode == "replay":
                description = description.replace("人工", "演练")
            if record["action"] in {"remeasure", "invalidate_wiring"} or record["kind"] == "agent.recheck":
                emitted.clear()
            # Repeated identical successful checks do not swamp the process table.
            key = (title, description)
            if key in emitted and record["action"] not in {
                "pause",
                "resume",
                "remeasure",
                "invalidate_wiring",
            }:
                continue
            emitted.add(key)
            chosen = next((f for f in figures if f.reference == reference), None)
            timeline.append(
                [when(record["at"]), title, description, figure_name(chosen) if reference else "—"]
            )
    for m in state.measurements:
        timeline.append(
            [
                when(m.at),
                "测量",
                f"记录{labels[m.id]}：{number(m.value)} {text(m.unit)}（{method(m)}）",
                "见测量表",
            ]
        )
    timeline.sort(key=lambda row: row[0])
    if timeline:
        sections[3].blocks.append(Table(["时间", "环节", "实际记录", "图像"], timeline))
    else:
        sections[3].blocks.append("未留存可整理的逐步执行记录；计划中的步骤不作为已执行记录。")
    cleanup = evidence_for("battery_cleanup")
    if cleanup:
        item = figure(cleanup[2], "实验收尾核对画面")
        if item:
            sections[3].blocks.append(item)

    data = Table(["记录", "采样时间", "测量值", "实际量程", "核验方式", "图像"], [])
    selected_figures = []
    for i, m in enumerate(active):
        linked = []
        supporting = stability(m)
        if i < 4:
            if supporting:
                for j, candidate in enumerate(supporting):
                    item = figure(
                        candidate.reference,
                        f"{labels[m.id]}第{j + 1}帧显示屏，{when(candidate.at)}",
                        candidate.roi,
                    )
                    if item:
                        linked.append(item)
            else:
                candidate = candidates.get(m.candidate_id)
                item = figure(
                    m.reference, labels[m.id] + "对应的显示屏记录", candidate.roi if candidate else ()
                )
                if item:
                    linked.append(item)
        selected_figures.extend(f for f in linked if f not in selected_figures)
        data.rows.append(
            [
                labels[m.id],
                when(m.at),
                f"{number(m.value)} {text(m.unit)}",
                range_for(m),
                method(m),
                "、".join(figure_name(f) for f in linked) if linked else "未留存或未选入正文",
            ]
        )
    sections[4].blocks.append(data if data.rows else "尚无有效已保存测量数据。")
    if any(stability(m) for m in active):
        sections[4].blocks.append("两帧稳定性核验共同支持一个测量样本，不计作两次独立测量。")
    if len(active) > 4:
        sections[4].blocks.append("表中保留全部有效样本，图像选取前四组作展示。")
    sections[4].blocks.extend(selected_figures)
    if unsaved:
        sections[4].blocks += [
            "以下读数已确认但尚未保存，不计入最终统计：",
            Table(
                ["记录", "时间", "测量值", "来源"],
                [[labels[m.id], when(m.at), f"{number(m.value)} {text(m.unit)}", method(m)] for m in unsaved],
            ),
        ]

    units = sorted({m.unit for m in active})
    if not active:
        sections[5].blocks.append("有效已保存样本不足，暂不计算均值或给出数值结论。")
    else:
        rows = []
        for unit in units:
            values = [m.value for m in active if m.unit == unit]
            rows.append(
                [
                    text(unit),
                    str(len(values)),
                    number(mean(values)),
                    number(min(values)),
                    number(max(values)),
                    number(max(values) - min(values)) if len(values) > 1 else "—",
                ]
            )
        sections[5].blocks.append(Table(["单位", "样本数", "平均值", "最小值", "最大值", "极差"], rows))
        if len(active) == 1:
            sections[5].blocks.append("本次仅有一个有效样本，只能描述该次读数，不作重复性分析。")
        if len(units) > 1:
            sections[5].blocks.append("不同单位分别统计，未进行自动换算或混合求平均。")
        sections[5].blocks.append("未记录仪表精度与校准信息，不据此计算测量不确定度。")

    problems = []
    visual_failures, pauses, resumes, restarts, rejected_actions = {}, 0, 0, 0, 0
    for record in records:
        if record["kind"] == "workflow.visual_check" and record["result"].get("passed") is False:
            rule = record["input"].get("rule", "")
            label = RULES.get(rule, {}).get("title", "现场条件核对")
            visual_failures[label] = visual_failures.get(label, 0) + 1
        if record["accepted"]:
            pauses += record["action"] == "pause"
            resumes += record["action"] == "resume"
            restarts += (
                record["action"] in {"remeasure", "invalidate_wiring"} or record["kind"] == "agent.recheck"
            )
        elif record["result"].get("accepted") is False:
            rejected_actions += 1
    for label, count in visual_failures.items():
        problems.append([label, f"有 {count} 次未满足条件或未看清；这些检查未作为通过依据。"])
    unusable = sum(c.value is None for c in state.candidates)
    polarity_rejected = [c for c in state.candidates if c.status == "rejected"
                         and c.reason == reading_issue(plan, c.value) and c.reason]
    rejected = sum(c.status == "rejected" for c in state.candidates) - len(polarity_rejected)
    if unusable:
        problems.append(["读数识别", f"有 {unusable} 次未取得可用数值，未计入测量结果。"])
    if rejected:
        problems.append(["人工拒绝", f"有 {rejected} 条候选读数被拒绝，未采用为正式结果。"])
    if polarity_rejected:
        values = "、".join(number(c.value) + " V" for c in polarity_rejected[:8])
        problems.append(["极性与有效电压检查", f"读数 {values} 为负数或零值，已提示检查表笔连接和接触，未计入正式测量。"])
    if pauses or resumes:
        problems.append(["暂停与恢复", f"记录暂停 {pauses} 次、恢复 {resumes} 次。"])
    if restarts:
        problems.append(["复查与重测", f"共记录 {restarts} 次返回复查或重新测量。"])
    if rejected_actions:
        problems.append(["未执行的操作请求", f"有 {rejected_actions} 次请求未通过流程条件，未据此推进。"])
    if problems:
        sections[6].blocks.append(Table(["事项", "实际处理记录"], problems))
    corrections = []
    for m in state.measurements:
        changed = m.original_value is not None and (
            m.original_value != m.value or math.copysign(1, m.original_value) != math.copysign(1, m.value)
        )
        if changed:
            note = next(
                (
                    r["input"].get("note")
                    for r in records
                    if r["accepted"]
                    and r["action"] == "confirm_candidate"
                    and r["input"].get("candidate_id") == m.candidate_id
                    and r["input"].get("note")
                ),
                None,
            )
            corrections.append(
                [
                    labels[m.id],
                    f"{number(m.original_value)} {text(m.unit)}",
                    f"{number(m.value)} {text(m.unit)}",
                    text(note) if note else "修正说明未记录",
                ]
            )
    if corrections:
        sections[6].blocks += ["人工修正：", Table(["记录", "原识别值", "最终采用值", "说明"], corrections)]
    replaced = [m for m in state.measurements if m.superseded]
    if replaced:
        sections[6].blocks += [
            "以下历史读数已被重测替代，保留记录但不纳入统计：",
            Table(
                ["记录", "时间", "原测量值", "状态"],
                [
                    [labels[m.id], when(m.at), f"{number(m.value)} {text(m.unit)}", "已被替代"]
                    for m in replaced
                ],
            ),
        ]
    if not problems and not corrections and not replaced:
        sections[6].blocks.append("未记录异常处理或人工修正事项。")

    if state.status == "completed" and (not measurement or (target and len(active) >= target)):
        sections[7].blocks.append(
            "本次计划流程已完成。"
            + (f"已取得 {len(active)} 个有效已保存样本。" if measurement else "本计划未设置数值采样任务。")
        )
    else:
        if state.status == "completed":
            sections[7].blocks.append(
                "会话标记为已完成，但有效已保存样本尚未达到计划要求，不能认定采样任务已完成。"
            )
        else:
            sections[7].blocks.append(
                f"本次实验{status}，当前停留在第 {state.step + 1}/{len(steps)} 步“{text(steps[state.step]['title'])}”。"
            )
        if measurement:
            sections[7].blocks.append(
                f"计划取得 {target} 个有效样本，目前已保存 {len(active)} 个；未保存或已被替代的读数不计入最终结果。"
            )
        if state.status != "completed":
            sections[7].blocks.append(
                "当前及后续待完成步骤：" + "、".join(text(s["title"], 60) for s in steps[state.step :]) + "。"
            )
        elif measurement and not active:
            sections[7].blocks.append("会话虽标记完成，但缺少有效已保存测量，无法形成测量结论。")
    if state.status == "paused":
        sections[7].blocks.append("暂停原因：" + text(state.pause_reason) + "。")
    for unit in units:
        values = [m.value for m in active if m.unit == unit]
        sections[7].blocks.append(
            (
                f"本次记录的结果为 {number(values[0])} {text(unit)}。"
                if len(values) == 1
                else f"本次有效读数为 {number(min(values))}～{number(max(values))} {text(unit)}，平均值 {number(mean(values))} {text(unit)}。"
            )
        )
    if measurement and measurement.get("adapter") == "voltage" and "电池" in " ".join(object_labels.values()):
        sections[7].blocks.append("结论仅覆盖本次实测电压，不据此推断电池容量或寿命。")
    return Document(text(state.title, 120) + " · 实验报告", sections, state.mode == "replay")
