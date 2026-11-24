import csv
import io


def markdown_report(state, spec, events):
    replay = state.mode == "replay"
    lines = [f"# {state.title}", "", f"实验 ID：{state.id}",
             f"类型：{'软件回放测试（非真实测量）' if replay else '真实实验会话（见每条数据来源）'}",
             f"状态：{state.status}；当前步骤：{state.step + 1}/9", f"创建时间（UTC）：{state.created_at}", "",
             "> 本系统是研究原型，不构成电气安全证明。人工确认不是硬件自动验证。",
             "> 仅记录已经取得的数据；未接入设备/模型时不代表对应能力已验证。", "",
             "## 测量记录", "", "| 数值 | 单位 | 来源 | 时间 UTC | 已保存 | 已被替代 | 参考 |",
             "|---|---|---|---|---|---|---|"]
    for m in state.measurements:
        reference = m.reference.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {m.value:g} | {m.unit} | {m.source} | {m.at} | {m.saved} | {m.superseded} | {reference} |")
    if not state.measurements:
        lines.append("| 无测量数据 | — | — | — | — | — | — |")
    lines += ["", "## 最近确认与证据", ""]
    for name, evidence in state.facts.items():
        lines.append(f"- {name}: {evidence.value}；来源 {evidence.source}；时间 {evidence.at}")
    lines += ["", "## 操作审计", "", "详细、完整结构化日志请使用 JSON 导出；以下仅为事件摘要。", ""]
    for event in events:
        result = event["payload"].get("result", {})
        lines.append(f"- {event['sequence']} · {event['at']} · {event['kind']} · "
                     f"{result.get('message', '')}")
    lines += ["", "## 限制", "", spec["warning"],
              "本报告不计算未经标注数据集验证的识别准确率，也不声称已完成 P1 NPU 性能测试。", ""]
    return "\n".join(lines)


def measurements_csv(state):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["session_id", "mode", "measurement_id", "value", "unit", "source", "at", "saved", "superseded"])
    for m in state.measurements:
        writer.writerow([state.id, state.mode, m.id, m.value, m.unit, m.source, m.at, m.saved, m.superseded])
    return "\ufeff" + buffer.getvalue()
