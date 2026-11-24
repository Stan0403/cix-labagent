"""Build the Chinese evidence-based whitepaper; requires reportlab."""
import json
import os
from pathlib import Path
import sys
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Flowable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from summarize_evidence import summary

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/CIX-LabAgent-Technical-Whitepaper.pdf'
FONT = os.getenv('CIX_WHITEPAPER_FONT', 'C:/Windows/Fonts/simhei.ttf')
if Path(FONT).is_file():
    pdfmetrics.registerFont(TTFont('ZH', FONT))
else:
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    FONT = 'STSong-Light'
F = 'ZH' if FONT != 'STSong-Light' else FONT
NAVY = colors.HexColor('#132C46')
TEAL = colors.HexColor('#008F91')
MUTED = colors.HexColor('#586B7B')
LIGHT = colors.HexColor('#EDF5F6')
styles = {
    'title': ParagraphStyle('title', fontName=F, fontSize=24, leading=33, textColor=NAVY, spaceAfter=16, wordWrap='CJK'),
    'h1': ParagraphStyle('h1', fontName=F, fontSize=20, leading=28, textColor=NAVY, spaceAfter=16, wordWrap='CJK'),
    'h2': ParagraphStyle('h2', fontName=F, fontSize=12, leading=18, textColor=TEAL, spaceBefore=13, spaceAfter=7, wordWrap='CJK'),
    'body': ParagraphStyle('body', fontName=F, fontSize=10.2, leading=17, textColor=NAVY, spaceAfter=9, wordWrap='CJK'),
    'small': ParagraphStyle('small', fontName=F, fontSize=8, leading=12.5, textColor=MUTED, spaceAfter=7, wordWrap='CJK'),
    'cell': ParagraphStyle('cell', fontName=F, fontSize=9, leading=14, textColor=NAVY, wordWrap='CJK'),
    'head': ParagraphStyle('head', fontName=F, fontSize=9, leading=14, textColor=colors.white, wordWrap='CJK'),
    'code': ParagraphStyle('code', fontName=F, fontSize=8.3, leading=14, textColor=NAVY, backColor=LIGHT, borderPadding=8, spaceAfter=10, wordWrap='CJK'),
}
story = []


def para(text, style='body'):
    return Paragraph(escape(str(text)).replace('\n', '<br/>'), styles[style])


def add(text, style='body'):
    story.append(para(text, style))


def table(headers, rows, widths, padding=8):
    content = [[para(x, 'head') for x in headers]] + [[para(x, 'cell') for x in row] for row in rows]
    t = Table(content, colWidths=widths, hAlign='LEFT', repeatRows=1)
    t.setStyle(TableStyle([
      ('BACKGROUND', (0, 0), (-1, 0), NAVY), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
      ('ROWBACKGROUNDS', (0, 1), (-1, -1), [LIGHT, colors.white]),
      ('LEFTPADDING', (0, 0), (-1, -1), 9), ('RIGHTPADDING', (0, 0), (-1, -1), 9),
      ('TOPPADDING', (0, 0), (-1, -1), padding), ('BOTTOMPADDING', (0, 0), (-1, -1), padding),
      ('LINEBELOW', (0, 0), (-1, 0), 1, TEAL),
      ('LINEBELOW', (0, 1), (-1, -1), 0.25, colors.HexColor('#D6E3E7')),
    ]))
    story.extend([t, Spacer(1, 11)])


def page(number, title, subtitle):
    if story:
        story.append(PageBreak())
    add(f'{number:02d}  /  CIX LABAGENT', 'small')
    add(title, 'h1')
    add(subtitle, 'small')


class Architecture(Flowable):
    def __init__(self):
        super().__init__()
        self.width = 490
        self.height = 210

    def draw(self):
        c = self.canv
        def box(x, y, w, h, title, detail, fill=LIGHT):
            c.setFillColor(fill); c.roundRect(x, y, w, h, 7, fill=1, stroke=0)
            c.setFont(F, 10); c.setFillColor(NAVY); c.drawCentredString(x+w/2, y+h-19, title)
            c.setFont(F, 8); c.setFillColor(MUTED); c.drawCentredString(x+w/2, y+12, detail)
        box(0, 152, 490, 51, '浏览器相机 / 文档 / 麦克风 / 现场人工核对', '实际访问设备采集；图像与模型输出只形成有来源的证据')
        box(0, 84, 490, 51, 'CPU：Web + 规则门控 + 事务 + 知识检索 + 报告', '会话 / 步骤 / 版本 / 时效 / 采集编号；模型不能直接写最终测量')
        box(0, 0, 151, 61, 'CPU 语言与 OCR', 'Qwen3 / OCR / VLM 文本')
        box(169, 0, 151, 61, 'CIX NPU 专用网络', 'YOLO / SenseVoice / 视觉编码')
        box(338, 0, 152, 61, 'SQLite + 证据图片', '审计 / 历史 / 图文报告')
        c.setStrokeColor(TEAL); c.setLineWidth(1.2)
        for x in [75, 245, 414]:
            c.line(x, 84, x, 65); c.line(x, 65, x-3, 70); c.line(x, 65, x+3, 70)
        c.line(245, 152, 245, 139)


class CPUChart(Flowable):
    def __init__(self):
        super().__init__()
        self.width = 490
        self.height = 156

    def draw(self):
        c = self.canv
        data = [('简短中文', 4.576, 2.705), ('带资料问答', 23.187, 14.654), ('工具调用', 7.660, 4.398)]
        for i, (label, old, new) in enumerate(data):
            y = 126-i*45
            c.setFillColor(NAVY); c.setFont(F, 9); c.drawString(0, y, label)
            for j, (value, color) in enumerate([(old, colors.HexColor('#B5C5D1')), (new, TEAL)]):
                c.setFillColor(color); c.rect(96, y-2-j*14, value*12, 10, stroke=0, fill=1)
                c.setFont(F, 8); c.setFillColor(NAVY); c.drawString(101+value*12, y-j*14, f'{value:.3f} s')
        c.setFont(F, 8); c.setFillColor(MUTED); c.drawString(96, 1, '灰：原 CPU    绿：优化 CPU    每类 n=2，完整 HTTP 响应')


metrics = summary()
page(1, 'CIX LabAgent\n技术白皮书', '代码、部署、异构算力与真实测量证据 | 版本 2026.11.24')
story.append(Spacer(1, 24))
add('面向电池电压实验的本地多模态助手', 'title')
add('以 CPU 规则与事务约束实验流程，以 NPU 执行已验证的专用网络，并以真实后端、图像来源和测量证据记录每次操作。')
table(['交付内容', '本稿范围'], [
 ['完整当前应用源码', '保留本地最新修改，附旧版、研究代码、逐文件来源与性能原始 JSON。'],
 ['可复现部署', '普通电脑一键启动；O6 在模型、厂商运行时和工件锁就绪后启动服务栈。'],
 ['真实性能', '记录 CPU / GPU / NPU 实测条件、成功和失败路线，不混淆局部与全链路耗时。'],
 ['关键限制', '当前桌面缺 YOLO 等部分板端工件；OCR 开发图参与训练；没有独立全场景准确率。'],
], [122, 368])
add('证据截止：2026-11-24。硬件性能来自既有 O6 板端记录；本次另在电脑运行 344 项软件回归。未将历史性能重新描述为本次新测，也未在本次操作中改变正式板端服务。', 'small')
add('参考平台：Radxa Orion O6 / CIX P1，32 GB，ARM64 Linux。源码入口：根目录 README 与 docs/CURRENT_STATE.md。GitHub 私有仓库：https://github.com/Stan0403/cix-labagent 。通过 Git 与官方 CLI 发布，无需插件。', 'small')

page(2, '系统设计与可信执行边界', '模型提供建议；程序保存可追溯的实验事实。')
story.append(Architecture())
add('六步电池实验', 'h2')
add('阅读实验要求 → 核对器材 → 检查仪表 → 连接并读表 → 保存 → 生成报告。原有计划与历史事件仍可读取；正向电池路径不再由大模型猜测表笔接触或物理收尾是否完成。')
add('计划、现场证据与测量分开', 'h2')
add('计划由文档提取和受约束语言模型生成；器材、量程及读数需要现场证据或明确人工输入。模型输出经类型、范围、工具白名单和规则检查后才能影响状态。TF-IDF 在 CPU 检索本地手册，当前未部署 BGE 语义编码。')
add('最终写入在事务中完成', 'h2')
add('会话、当前步骤、revision 和证据有效期共同约束操作。切换、暂停、刷新或删除使过期结果失效；创建或恢复实验暂停其他运行会话。模型请求结束不等于自动获得提交权限。')
add('来源：labagent/rules.py、task_agent.py、store.py、demo_api.py；电池规则及会话生命周期见专项文档。图中的箭头为逻辑调用，不代表硬件同时并发。', 'small')

page(3, '模型部署与运行时矩阵', '以代码、服务环境和实际后端字段为准，区分现行部署与历史方案。')
table(['模块 / 模型', '实际计算位置', '接口 / 运行条件'], [
 ['Qwen3-4B Q4_K_M', 'CPU，KleidiAI，大核 8 线程', '8081；8192 上下文；单并发；关闭思考输出'],
 ['YOLO26n INT8 四类', 'CIX NPU，CPU 预处理与 NMS', '8035；960 输入；阈值 0.35；电池/仪表/红黑表笔'],
 ['PP-OCR LCD v3', 'CPU ONNX Runtime，4 线程', '8031 读表；像素符号/小数点检查；不确定转 VLM'],
 ['Qwen3-VL-4B', '通用路线 CPU；仪表路线 NPU 视觉 + CPU 文本', '8032 / 8034；语言 GGUF + FP16 projector；NPU 固定 320 输入'],
 ['SenseVoiceSmall .cix', 'NPU 网络；CPU 音频前后处理', '8011；16 kHz；固定形状/分段；独立 NumPy 1.26.4 环境'],
 ['规则 / 检索 / 数据库', 'CPU', '8001；FastAPI/Python/SQLite；字符 n-gram TF-IDF'],
], [135, 147, 208])
add('固定构建和工件', 'h2')
add('llama.cpp 固定提交 57819b8d4b39d893408e51520dff3d47d1ebb757。NPU VLM 在同一版本加入 C++ Unix socket 视觉桥，CIX 工作者持有 /dev/aipu，四路特征各为 100×2560。完整 VLM 文本生成没有搬到 NPU。')
add('部署需要工件校验', 'h2')
add('OCR v3 SHA-256 为 ec0b3f0b…e8655d35，完整摘要在 deploy/model_manifest.json。视觉编码器 SHA-256 为 fd315596…d7e4d0。清单缺失的摘要不会猜填；源板导出包生成 ARTIFACT_LOCK.json 后才允许完整部署。')
add('来源：E01、E02、E03、E04。旧专项文档可能仍描述 VLM 器材路径；当前器材使用 YOLO，当前仪表页采用单帧拍照核验。', 'small')

page(4, '异构算力调用与错误处理', '这是按任务和已验证能力分流的服务架构，没有自动成本预测或通用负载调度器。')
table(['任务', '调用逻辑', '失败 / 不确定时'], [
 ['器材核对', '浏览器 → 8001 → 8031 /inspect → 8035 → NPU YOLO → CPU 解码/NMS', '未知器材、缺一支表笔、低可信度保留待核对；两次合规采集才满足稳定证据。'],
 ['仪表设置', 'YOLO 找仪表 → 裁剪旋钮/插孔 → NPU 320视觉 → CPU 受约束短文本', '区域小、遮挡、不确定不通过；NPU 不可用报错/人工核对，不伪装已迁移。'],
 ['液晶读数', '自适应定位 LCD → CPU OCR → 源像素检查 → 必要时 CPU VLM', '数字位冲突拒收；空串未知；不按电池常识补小数点、符号或缺失数字。'],
 ['语音', '浏览器录音 → Web → ASR CPU 解码/特征 → NPU → CPU CTC', '无效/过长音频报错；繁忙拒绝并发；不静默改用云端或模拟转写。'],
 ['语言决策', 'CPU Qwen3 规划/对话；规则验证工具参数和状态', '缺少证据或工具结果不满足 gate 时停留；人工事实不能由模型代填。'],
], [73, 212, 205])
add('背压与实际交互', 'h2')
add('轻量器材预览限制单请求在途，避免堆积旧视频帧。当前仪表单帧拍照方案在一次检查中使用同一照片的区域；用户改变现场后需重新拍照。结果明确标记 single_frame_snapshot，不能称为持续追踪当前现场。')
add('配置名与硬件来源', 'h2')
add('历史 ocr_provider=mnn 只是 HTTP 适配协议名。检查 reading_backend、reading_ready、YOLO 后端和 NPU worker 的请求计数，才能判断实际路线；健康接口 200 本身仍不能替代真实推理验收。')
add('来源：scripts/qwen3vl_gguf_service.py、yolo_equipment_service.py、cix_npu_vision_worker.py、labagent/local_asr.py 和 E03/E04。', 'small')

page(5, '语言模型：CPU 优化与 GPU 对照', '2026-11-17；三类固定请求，每配置每类两轮；温度 0、固定种子、关闭 prompt 缓存、最大 96 token。')
story.append(CPUChart())
rows = []
for case, label in [('short', '简短中文'), ('manual', '带资料问答'), ('tool', 'get_status 工具')]:
    variants = metrics['language_http_complete_response']
    old = variants['llm_existing_cpu'][case]['mean_seconds']
    new = variants['llm_optimized_cpu'][case]['mean_seconds']
    gpu = variants['llm_vulkan'][case]['mean_seconds']
    rows.append([label, f'{old:.3f}', f'{new:.3f}', f'{gpu:.3f}', f'{(1-new/old)*100:.1f}%'])
table(['请求', '原CPU /秒', '新CPU /秒', 'Vulkan /秒', 'CPU降幅'], rows, [108, 95, 95, 97, 95])
add('为何选择 CPU', 'h2')
add('优化运行时启用 KleidiAI 与 ARM 指令，并固定 A720 大核。新 CPU prompt 处理约 41–60 token/s，旧约 24–25；生成仍约 13–15 token/s。GPU 确实卸载了 37/37 层，但 prompt 约 3–5 token/s，本板完整请求更慢，因此没有进入正式语言路径。')
add('这些数字能说明什么', 'h2')
add('三组平均完整响应延迟下降约 37%–43%，说明该固定工作负载受益。每组只有两轮，输出长度、缓存、上下文和同时运行的视觉服务会影响结果；不推导稳定 P95、整套实验提速或硬件理论算力。passed 仅检查响应/工具协议，不代表模型建议的实验知识正确性。')
add('原始数据：E01。本页表格由 scripts/summarize_evidence.py 从逐次 JSON 重新计算，不引用厂商宣传跑分。', 'small')

page(6, '视觉与 OCR：速度、精度和数据边界', '读数可用性取决于符号、小数点、量程和拒识，不能只比较裸模型耗时。')
table(['测量', '实测结果', '样本和证据等级'], [
 ['OCR 定位+识别库调用', '81 张：78 快读、3 回退；中位 0.294 s，P90 0.520 s', '原始 JSON 可复核；该批照片全部参与训练，不是独立测试准确率。'],
 ['OCR 正式 HTTP 小样本', '3 张快速场景约 0.28–0.53 s；回退场景约 1.24–1.94 s', '留存图功能/路由验证；不代表现场最大耗时。'],
 ['YOLO 纯 NPU', '均值 25.85 ms；含前后处理 69.37 ms', '25 张验证图的部署文档转录；原始逐图文件不在桌面交付。'],
 ['YOLO 检出计数', 'TP 70、FP 0、FN 6；漏检均为电池', '仅特定验证集与阈值的历史记录，不外推总体效果；不等同于完整 mAP。'],
], [112, 184, 194])
add('OCR 训练过程', 'h2')
add('从 CIX 提供的 ResNet34-vd + 双向 LSTM + 6625类 CTC 图恢复浮点权重。第三轮从 v2 初始化，1200 步约 326 秒，实拍训练共 129 张；新批次包含 81 张、51 种字符串、34 张负值。只训练选定 CNN/CTC 参数，其余部分冻结；导出后比较 PyTorch/ONNX 输出。')
add('训练拟合和诊断必须分开', 'h2')
add('新批次拟合 66/81 → 81/81，合成验证 332/400 → 341/400；旧数字诊断 2/4 → 3/4、空屏 4/4。旧样本标签由 4.19 修正为 .419，模型仍可能读成 41.9，因此源像素检查阻止直接接受。合成验证、开发中反复查看的小诊断集都不能替代新采集的独立现场测试。')
add('来源：E03、E05、E06。OCR 当前强制 CPUExecutionProvider，尚未把第三轮权重编译为 .cix。', 'small')

page(7, '仪表、语音与 NPU 局部实验', '分别标记网络推理、混合链路和完整业务响应；不把单算子收益写成整模型收益。')
table(['实验', '真实记录', '解释'], [
 ['仪表混合路线', '两张留存图 3.657 / 3.610 s；正式接口样例约 3.651 s', 'YOLO 定位 + NPU视觉 + CPU短文本。结果含不确定/未通过，不是已验证物理档位准确率。'],
 ['视觉编码器探针', '三次约 0.460 / 0.464 / 0.457 s', '仅编码器；模型加载约 0.365 s。完整业务还需文本生成、裁剪和接口。'],
 ['SenseVoice ASR', '5.616 s 中文音频处理约 0.325 s', '历史部署文档记录；含特征/推理/后处理，不含加载、上传和解码。'],
 ['Qwen3 首层 FP16', '三条提示词首层预填充误差约 0.0052%–0.0060%', '仅第0层 Q投影在NPU；其余计算 NumPy CPU；没有完整文本生成。'],
 ['W8A8 Q投影', '预填充中位 CPU 14.1 → 混合 4.5 ms；单token 2.2 → 3.8 ms', '相对 NumPy/OpenBLAS 4线程，不是正式 llama.cpp；首层量化误差未通过实验门槛。'],
], [109, 186, 195])
add('失败经验也是结论', 'h2')
add('Qwen3 完整首层预填充中位只由 254.3 降至 245.7 ms；W8A8 首层误差约 0.71%–0.81%，超过本实验自定 0.5% 门槛。单 token 反而变慢。alpha=0.5 激活平滑未改善；FP16 MatMul 发生 CPU 回退，QDQ MatMul 编译失败，tinygrad 旧驱动 smoke 超时。')
add('下一步的范围', 'h2')
add('完整 NPU 迁移仍缺全部 36 层、输出头、不同预填充长度、长 KV、生成和工具协议质量验证。当前只支持研究预填充大矩阵的可能收益；不能替换正式 CPU8081。ASR 示例也出现错字，尚无自有实验口令独立准确率。')
add('来源：E02、E07、E08、E09。语音与 YOLO 的部分指标仅有部署文档级证据，白皮书明确保留这一证据缺口。', 'small')

page(8, '优化经验：减少无效计算和错误接受', '先保证测量含义，再评估延迟；优化不能抹掉拒识、时效或人工核对。')
table(['改进', '依据与实践', '保留的约束'], [
 ['专用任务用小模型', '器材由慢 VLM 改为 YOLO NPU；OCR 专注液晶数字，复杂场景回退', '未知类别与数字位冲突不自动接受；不能套用通用 COCO 类别。'],
 ['CPU 运行时调优', '固定提交、KleidiAI、ARM指令和大核亲和性；用同组请求比较', '不假定GPU一定快；缓存/输出长度变化需单独报告。'],
 ['减少语言决策负担', '压缩重复上下文、限制输出和工具；已审核的局部步骤由规则推进', '不能把未完成的现场事实写为完成，也不把局部优化覆盖到所有Agent阶段。'],
 ['约束视觉与前处理', '固定编码尺寸、裁剪旋钮与插孔；OCR 等比缩放加右侧补边', '负号/小数点必须来自像素，裁剪丢失信息时拒收或回退。'],
 ['单请求与结果绑定', '避免排队旧帧；响应绑定会话/步骤/版本；页面变化后取消调度', '刷新、暂停和删除不能让旧模型结果推进当前实验。'],
 ['实测决定上线', 'GPU与部分NPU量化路线留作实验；新OCR仅以CPU发布', '实际 provider、设备句柄、编码计数和数值对齐需可核验。'],
], [107, 198, 185])
add('旧 OCR 前处理的诊断', 'h2')
add('同一通用 NPU OCR 权重，在两种字体共 12 个合成短文本上，强制拉伸完整链路仅 2/12 精确匹配，等比缩放补边后 11/12；直接给完整文字区域可到 12/12。负号仍可能被检测框裁去，说明识别置信度不能弥补前处理丢信息。该历史诊断不代表当前液晶模型现场准确率。')
add('来源：E01、E05、E10；agent_optimization/comparison.json 保存局部决策样例，不把单个缓存命中收益写成恒定加速比例。', 'small')

page(9, '真实测量、数据保存与回归验证', '模型识别结果、软件 fixture 与物理真实测量是三类不同证据。')
add('电池极性与稳定读数', 'h2')
add('正向电池实验拒绝负值、负零与零值进入正式样本，保留候选与照片并提示调整。有效正读数还需满足计划范围、量程、证据时效与两次稳定采集。允许静止画面的两张图片字节相同，但必须是新的相机采集编号、间隔至少 1 秒；重复提交同一编号不算第二次采集。两帧差值不超过 0.02 V，最终只新增一条正式测量。')
add('保存、恢复和删除', 'h2')
add('保存后显示持久化的 5 秒收尾提示，提醒移开表笔和关闭万用表；期间不调模型，也不伪装已视觉确认物理收尾。刷新与恢复先暂停现场执行，删除明确确认并校验版本。图文报告使用已保存且未被替代的测量，明确区分更正、回放和缺失记录。')
table(['验证类别', '本次/历史结果', '不能据此推出'], [
 ['当前电脑软件回归', '344 项通过，0失败/0错误/0跳过；约113.736 s', '不是344次真机模型推理，也不是344次真实实验。'],
 ['历史板端电池完成阶段', '310项回归及9类页面流程记录', '当前本地344项和历史310项不是同一次运行，不相加。'],
 ['真实模型诊断', '留存OCR符号/数值、YOLO路由、NPU仪表链路等', '小样本已查看，不代表独立准确率或长期可靠性。'],
 ['新O6安装器', '生成服务与预检逻辑；本次做软件/配置层验证', '未在干净O6上执行安装、整机重启或长时间并发验收。'],
], [111, 191, 188])
add('另在不继承系统包的 Python 3.12.7 新环境完成344项回归，依赖检查通过；README一键安装与隔离数据启动均通过，网页和健康接口返回200。来源：E11、E12。未执行的 GitHub CI 和 Docker 构建不标记为通过。', 'small')

page(10, '可复现部署与完整交付条件', '提供能直接执行的入口，也列明无法从当前电脑补出的工件。')
table(['路径', '依赖', '执行入口'], [
 ['普通电脑', 'Python3.11/3.12、包源、浏览器', 'python quickstart.py；默认规则与人工流程，8001端口'],
 ['容器Web', 'Docker + Compose，访问令牌', 'docker compose up -d --build；数据命名卷；本次未实测构建'],
 ['完整O6', '匹配CIX镜像/驱动、模型包、独立ASR/NPU环境、固定llama.cpp', 'python deploy/build_runtime.py；准备后 o6_stack.py --apply'],
 ['源板工件导出', '现有全部模型目录，包含许可证及前后处理文件', 'deploy/export_models.py 生成ZIP与ARTIFACT_LOCK.json'],
 ['GitHub发布', 'Git + gh官方登录；无需插件', 'deploy/publish_github.ps1；默认私有，成功后输出实际URL'],
], [100, 184, 206])
add('源码与模型分别交付', 'h2')
add('源码仓库含当前应用、旧版、OCR训练、NPU实验、维护代码、构建桥、测试和证据。OCR v3 权重约111.6 MB，另附ZIP并提供摘要，不塞进普通Git。当前电脑缺自训练YOLO .cix、完整VLM/ASR权重及原采集批次，必须从源板导出；完整异构复现尚受这些外部工件约束。')
add('复现不是复制旧虚拟环境', 'h2')
add('在目标系统创建新环境；通用依赖锁定版本，厂商wheel与NPU驱动使用镜像匹配版本。跨架构不能复制Windows venv到ARM Linux。新的服务名使用 cix-labagent-*，端口被占或工件缺失时先报错，避免误接管旧正式栈。')
add('仍需补测', 'h2')
add('新采集独立数据的读数/档位准确率、置信区间、按场景拒识率、P50/P95全链路延迟、NPU与ASR并发、长期稳定性、峰值内存、功耗/能耗与整机重启恢复。当前没有这些数据，不填写估计数值。')

page(11, '证据索引与复核方法', '编号对应本包内的实际文件；文档记录与原始测量分级说明。')
refs = [
 ['E01', 'evidence/model_acceleration/2026-11-17/llm-benchmark.json', '逐次原始JSON；每配置每类n=2'],
 ['E02', 'evidence/model_acceleration/2026-11-17/npu-vision-probe.json', '编码器探针；另有numerical-repeat文件'],
 ['E03', 'docs/LCD_READER.md；evidence/ocr_training/production-reader/native-report.json', '部署说明 + 81图库调用原始JSON'],
 ['E04', 'docs/LIVE_METER.md；docs/yolo26-npu-deployment.md', '当前交互说明；YOLO指标为部署文档级证据'],
 ['E05', 'research/ocr_training/README.md；evidence/ocr_training/runs/lcd-v3-user81/report.json', '训练步骤、数据边界与结果'],
 ['E06', 'evidence/ocr_training/production-reader/formal-verification.json', '正式HTTP小样本验证'],
 ['E07', 'evidence/meter_setup_review/2026-11-24/final/validation/meter-pipeline.json', '仪表保留图混合全链路JSON'],
 ['E08', 'docs/LOCAL_ASR.md', '历史板端部署说明；未附原始音频准确率评测'],
 ['E09', 'evidence/npu_llm/2026-11-24/results/；research/npu_llm/README.md', '首层/量化原始JSON与数值门槛'],
 ['E10', 'evidence/model_acceleration/2026-11-17/npu-ocr-after-fix.json', '旧通用OCR前处理诊断'],
 ['E11', 'evidence/delivery/verification.json；baseline-tests.xml', '本次电脑源码回归与交付检查'],
 ['E12', 'evidence/battery_polarity/2026-11-24/completion/', '历史部署阶段软件和页面验证'],
]
table(['编号', '相对路径', '证据含义'], refs, [38, 294, 158], padding=4)
add('复核命令', 'h2')
add('python scripts/summarize_evidence.py\npython -m pytest --junitxml=test-results.xml\npython deploy/verify_models.py\npython scripts/build_whitepaper.py', 'code')
add('上游固定版本：llama.cpp 57819b8d…；CIX model hub f0ee7fef…；Qwen3-VL GGUF 625828cb…。完整提交、权重摘要和第三方许可见仓库清单。白皮书不使用产品标称TOPS替代实测，也不把文件存在或接口健康等同于模型质量验收。', 'small')


def chrome(canvas, doc):
    width, height = A4
    canvas.setFillColor(TEAL); canvas.rect(0, height-8, width, 8, fill=1, stroke=0)
    canvas.setFont(F, 8); canvas.setFillColor(MUTED)
    canvas.drawString(52, height-35, 'CIX LabAgent  /  技术白皮书')
    canvas.drawRightString(width-52, height-35, '2026.11.24')
    canvas.setStrokeColor(colors.HexColor('#D6E3E7')); canvas.line(52, 42, width-52, 42)
    canvas.drawString(52, 28, '来源可追溯 · 版本与证据范围见正文')
    canvas.drawRightString(width-52, 28, f'{doc.page:02d}')


if __name__ == '__main__':
    OUT.parent.mkdir(exist_ok=True)
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, rightMargin=52, leftMargin=52,
                            topMargin=62, bottomMargin=56,
                            title='CIX LabAgent 技术白皮书', author='CIX LabAgent Project',
                            subject='模型部署、异构算力、真实性能与复现边界')
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)
    print(OUT)
