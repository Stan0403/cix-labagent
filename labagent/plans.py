"""Document-grounded plans. Documents supply data; only allowlisted gates execute."""
import hashlib
import io
import json
import re
import threading
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from pydantic import Field, model_validator

from .battery_polarity import deferred_cleanup, deferred_contact, requires_positive
from .models import DomainError, StrictModel, now, uid


class Step(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,48}$")
    title: str = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=1800)
    gate: str = Field(pattern=r"^(read_sop|equipment|meter_setup|manual_check|measurement|save|report)$")
    source_line: int | None = Field(default=None, ge=1)
    origin: str = "document"
    visual_check: str = Field(default="auto", pattern=r"^(auto|manual|meter_setup|battery_contact|battery_cleanup)$")


class MeasureSpec(StrictModel):
    quantity: str = Field(default="电压", max_length=40)
    unit: str = Field(default="V", min_length=1, max_length=16)
    adapter: str = Field(default="manual", pattern=r"^(voltage|manual)$")
    min_value: float = -20
    max_value: float = 20
    samples: int = Field(default=1, ge=1, le=10)
    polarity_check: str = Field(default="auto", pattern=r"^(auto|positive|signed)$")

    @model_validator(mode="after")
    def limits(self):
        if self.min_value >= self.max_value:
            raise ValueError("测量下限必须小于上限")
        if self.adapter == "voltage" and (self.unit != "V" or self.min_value < -20 or self.max_value > 20):
            raise ValueError("当前自动读表只支持 ±20 V 内的低压直流电压")
        return self


class Plan(StrictModel):
    id: str = Field(default_factory=uid, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    title: str = Field(min_length=1, max_length=120)
    objective: str = Field(default="按要求完成实验并留下可追溯记录", max_length=1000)
    warning: str = Field(default="按实验文档和现场要求操作；无法判断时暂停并请求教师介入。", max_length=1500)
    required_objects: list[str] = Field(default_factory=list, max_length=20)
    object_labels: dict[str, str] = Field(default_factory=dict)
    steps: list[Step] = Field(min_length=2, max_length=24)
    measurement: MeasureSpec | None = None
    source_name: str = ""
    source_filename: str = ""
    source_sha256: str = ""
    source_text: str = Field(default="", max_length=40000)
    compiler: str = "document_rules"
    review_notes: list[str] = Field(default_factory=list)
    approved: bool = False
    created_at: str = Field(default_factory=now)
    input_mode: str = Field(default="document", pattern="^(goal|document|combined|template)$")
    questions: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def consistent(self):
        ids = [s.id for s in self.steps]
        gates = [s.gate for s in self.steps]
        if len(ids) != len(set(ids)) or len(self.required_objects) != len(set(self.required_objects)):
            raise ValueError("步骤或器材标识不能重复")
        if set(self.required_objects) != set(self.object_labels):
            raise ValueError("器材清单与名称必须对应")
        if gates[0] != "read_sop" or gates[-1] != "report" or gates.count("report") != 1:
            raise ValueError("流程必须以阅读要求开始，以报告结束")
        if "measurement" in gates:
            if gates.count("measurement") != 1 or gates.count("save") != 1 or self.measurement is None:
                raise ValueError("当前每个实验支持一个测量阶段和一个保存阶段，可包含多次采样")
            if gates.index("measurement") > gates.index("save"):
                raise ValueError("必须先测量再保存")
            if self.measurement.adapter == "voltage" and (
                "meter_setup" not in gates or gates.index("meter_setup") > gates.index("measurement")
            ):
                raise ValueError("电压读表前必须有万用表档位与插孔核对")
        elif self.measurement is not None or "save" in gates:
            raise ValueError("测量定义和保存步骤需要对应的测量阶段")
        data = self.model_dump()
        contacts = [s for s in self.steps if deferred_contact(data, s.model_dump())]
        cleanups = [s for s in self.steps if deferred_cleanup(data, s.model_dump())]
        if contacts:
            measure = next(s for s in self.steps if s.gate == "measurement")
            instruction = "\n".join([*(s.instruction for s in contacts), measure.instruction,
                                      "读出负数时调整红黑表笔连接；正数须通过两帧稳定性核验才记录，零值或读数不清时继续检查。"])
            measure.instruction = Step.model_validate({**measure.model_dump(), "instruction": instruction}).instruction
            self.steps = [s for s in self.steps if s not in contacts]
            self.review_notes.append("电池接触与极性检查已合并到读数环节；电压符号不作为无短接或接触位置的视觉证明。")
        if self.measurement and requires_positive(data):
            self.measurement.polarity_check = "positive"
        if cleanups:
            self.steps = [s for s in self.steps if s not in cleanups]
            self.review_notes.append("收尾视觉检查已移除；读数保存后显示5秒移开表笔、关闭万用表的提示，不核验物理完成情况。")
        return self


def extract_document(filename: str, raw: bytes) -> str:
    if not raw or len(raw) > 8_000_000:
        raise DomainError("请上传不超过 8 MB 的实验要求文档", 413)
    suffix = Path(filename).suffix.lower()
    try:
        if suffix in {".txt", ".md"}:
            text = raw.decode("utf-8-sig")
        elif suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            if len(reader.pages) > 30:
                raise ValueError("PDF 最多 30 页")
            text = "\n".join(f"[第 {i+1} 页]\n{p.extract_text() or ''}" for i, p in enumerate(reader.pages))
        elif suffix == ".docx":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                info = z.getinfo("word/document.xml")
                if info.file_size > 4_000_000:
                    raise ValueError("文档解压后过大")
                xml = ElementTree.fromstring(z.read(info))
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                text = "\n".join("".join(p.itertext()) for p in xml.findall(".//w:p", ns))
        else:
            raise ValueError("支持 UTF-8 TXT/Markdown、文字 PDF 和 DOCX")
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError("文档无法读取。请使用 UTF-8 文本、未加密文字 PDF 或 DOCX。", 422) from exc
    text = text.strip()
    if len(text) < 20 or len(text) > 40000:
        raise DomainError("正文需要 20–40000 字符；扫描 PDF 请先转换为文字", 422)
    return text


_NUMBERED = re.compile(r"^\s*(?:[-*]\s+|(?:步骤\s*)?(?:\d{1,2}|[一二三四五六七八九十]+)[.、:：)）]\s*|第[一二三四五六七八九十\d]+步[：:、.\s]*)")


def compile_document(text: str, filename: str) -> Plan:
    """Preserve the supplied operations, adding explicit platform verification steps."""
    lines = text.splitlines()
    title = next((x.strip().lstrip("# ") for x in lines if x.strip() and not x.startswith("[第")), "导入的实验")[:120]
    in_steps = False
    has_section = any(re.search(r"^(?:#+\s*)?(?:实验|操作)?(?:步骤|流程|方法)[：:\s]*$", x.strip()) for x in lines)
    operations = []
    equipment_text = ""
    for number, line in enumerate(lines, 1):
        clean = line.strip().lstrip("# ")
        if re.match(r"^(?:实验|操作)?(?:步骤|流程|方法)[：:\s]*$", clean):
            in_steps = True
            continue
        if re.match(r"^(?:实验)?(?:器材|材料|设备)[：:]", clean):
            equipment_text += clean.split("：", 1)[-1].split(":", 1)[-1]
        if re.match(r"^(?:注意事项|安全要求|报告要求|结果要求|实验原理)[：:\s]*$", clean):
            in_steps = False
        if (in_steps or not has_section) and _NUMBERED.match(line):
            body = _NUMBERED.sub("", line).strip()
            if body:
                operations.append((number, body))
        elif in_steps and operations and clean and not clean.startswith("[第"):
            n, body = operations[-1]
            operations[-1] = (n, (body + " " + clean)[:1800])
    if not operations:
        raise DomainError("没有识别出明确的操作步骤。请使用‘实验步骤’标题及 1.、2. 等编号，或在文本框中补充。", 422)
    if len(operations) > 19:
        raise DomainError("操作步骤过多，请先拆分为不超过 19 个主要步骤", 422)

    battery = "电池" in text and any(x in text for x in ["电压", "万用表"])
    voltage = any(x in text for x in ["测量电压", "读取电压", "电池电压", "直流电压"])
    labels = {}
    if battery:
        labels = {"battery": "1.5 V 干电池", "multimeter": "数字万用表", "probes": "红黑表笔"}
    elif equipment_text:
        items = [x.strip(" 。；") for x in re.split(r"[、，,;；]", equipment_text) if x.strip()]
        labels = {f"item_{i+1}": name[:80] for i, name in enumerate(items[:20])}
    steps = [Step(id="review", title="阅读并确认实验要求", instruction="阅读原始文档及拆解计划，确认与现场器材一致。", gate="read_sop", origin="platform")]
    if labels:
        steps.append(Step(id="equipment", title="核对所需器材", instruction="核对清单，可先拍照获取观察建议，再由现场人员确认。", gate="equipment", origin="platform"))
    measurement_seen = False
    setup_seen = False
    save_seen = False
    for number, body in operations:
        gate = "manual_check"
        if voltage and not setup_seen and any(x in body for x in ["直流电压档", "直流电压挡", "DCV", "20V档", "20 V 档", "COM"]):
            gate, setup_seen = "meter_setup", True
        elif not measurement_seen and any(x in body for x in ["读取", "读数", "记录电压", "读取电压", "测量电压"]):
            gate, measurement_seen = "measurement", True
        elif measurement_seen and not save_seen and any(x in body for x in ["保存", "记录结果", "记录读数", "记录测量"]):
            gate, save_seen = "save", True
        if re.fullmatch(r"(?:生成|整理|完成|导出)(?:实验)?报告[。.!！]?", body):
            continue
        steps.append(Step(id=f"doc_{number}", title=body.split("。")[0][:46], instruction=body[:1800], gate=gate, source_line=number))
    if voltage and not measurement_seen:
        steps.append(Step(id="measure", title="读取并核对电压", instruction="Agent 自动定位和裁剪显示屏，读取候选电压；请核对符号、小数点及单位后确认。", gate="measurement", origin="platform"))
        measurement_seen = True
    if voltage and not setup_seen:
        index = next(i for i, s in enumerate(steps) if s.gate == "measurement")
        steps.insert(index, Step(id="meter_setup", title="核对直流档位与表笔插孔", instruction="黑表笔接 COM，红表笔接 V/Ω 孔，选择适当直流电压量程。禁止使用电流档跨接电池。", gate="meter_setup", origin="platform"))
    if measurement_seen and not save_seen:
        steps.append(Step(id="save", title="核对并保存测量", instruction="核对全部测量与来源，确认保存；结果有疑问可重测。", gate="save", origin="platform"))
    steps.append(Step(id="report", title="生成实验报告", instruction="确认操作记录和测量无误，完成实验并导出报告。", gate="report", origin="platform"))
    nominal_match = re.search(r"(\d+(?:\.\d+)?)\s*[Vv伏]\s*(?:干|锂|蓄)?电池", text)
    nominal = float(nominal_match.group(1)) if nominal_match else None
    if battery:
        labels["battery"] = f"标称 {nominal:g} V 电池" if nominal is not None else "电池（请核对标称电压）"
        limit = 2 if nominal == 1.5 else 12 if nominal == 9 else 20
        measure = MeasureSpec(adapter="voltage", min_value=-limit, max_value=limit)
    else:
        measure = MeasureSpec(quantity="测量值", unit="待确认单位", min_value=-1000000, max_value=1000000) if measurement_seen else None
    if measure and voltage and not battery:
        measure = MeasureSpec(adapter="voltage")
    repeat = re.search(r"(?:重复|连续)(?:测量)?\s*([2-9])\s*次", text)
    if measure and repeat:
        measure.samples = int(repeat.group(1))
    notes = ["编号操作保留原文与行号；平台补充的核对、保存和报告步骤已标明。", "请审核器材、步骤、测量范围和安全要求后启用。"]
    if measure and measure.adapter == "manual":
        notes.append("此实验的测量需人工录入；当前视觉读表适配器只支持直流电压。")
    return Plan(title=title, steps=steps, measurement=measure, required_objects=list(labels), object_labels=labels,
                source_filename=Path(filename).name, source_text=text, source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                warning="仅按文档与已确认的器材操作；读数不清、操作异常或无法判断时暂停，必要时请教师介入。", review_notes=notes)


class Plans:
    def __init__(self, directory: Path, default_path: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        default = Plan.model_validate_json(default_path.read_text(encoding="utf-8"))
        if not (directory / (default.id + ".json")).exists():
            self.save(default)

    def save(self, plan: Plan):
        with self._lock:
            path = self.directory / (plan.id + ".json")
            temporary = path.with_suffix(".tmp")
            compatible=set()
            if plan.input_mode=="document":
                compatible.add("input_mode")
            if not plan.questions:
                compatible.add("questions")
            temporary.write_text(plan.model_dump_json(indent=2,exclude=compatible), encoding="utf-8")
            temporary.replace(path)
        return plan

    def get(self, plan_id):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", plan_id):
            raise DomainError("实验计划不存在", 404)
        try:
            return Plan.model_validate_json((self.directory / (plan_id + ".json")).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DomainError("实验计划不存在", 404) from exc

    def list(self):
        return [Plan.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(self.directory.glob("*.json"))]
