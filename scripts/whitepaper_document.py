"""Concise whitepaper backed by the 2026-11-27 board snapshot."""
from pathlib import Path
import json
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'evidence/board_refresh/2026-11-27'
BENCH = json.loads((E / 'model-benchmark.json').read_text(encoding='utf-8'))
CHECK = json.loads((E / 'final-source-check.json').read_text(encoding='utf-8'))
OUT = ROOT / 'docs/CIX-LabAgent-Technical-Whitepaper.pdf'
font = os.getenv('CIX_WHITEPAPER_FONT', 'C:/Windows/Fonts/msyh.ttc')
bold = os.getenv('CIX_WHITEPAPER_BOLD_FONT', 'C:/Windows/Fonts/msyhbd.ttc')
if Path(font).is_file():
    pdfmetrics.registerFont(TTFont('Body', font, subfontIndex=0))
    pdfmetrics.registerFont(TTFont('Head', bold if Path(bold).is_file() else font, subfontIndex=0))
    BODY, HEAD = 'Body', 'Head'
else:
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    BODY = HEAD = 'STSong-Light'
INK, GRAY = colors.HexColor('#242424'), colors.HexColor('#666666')
styles = {
 'title': ParagraphStyle('title', fontName=HEAD, fontSize=20, leading=28, textColor=INK, spaceAfter=8, wordWrap='CJK'),
 'section': ParagraphStyle('section', fontName=HEAD, fontSize=14, leading=21, textColor=INK, spaceBefore=8, spaceAfter=12, wordWrap='CJK'),
 'sub': ParagraphStyle('sub', fontName=HEAD, fontSize=10.8, leading=17, textColor=INK, spaceBefore=10, spaceAfter=5, wordWrap='CJK'),
 'body': ParagraphStyle('body', fontName=BODY, fontSize=10.3, leading=17, textColor=INK, spaceAfter=9, wordWrap='CJK'),
 'small': ParagraphStyle('small', fontName=BODY, fontSize=8.6, leading=13.5, textColor=GRAY, spaceAfter=7, wordWrap='CJK'),
 'cell': ParagraphStyle('cell', fontName=BODY, fontSize=9.2, leading=14.2, textColor=INK, wordWrap='CJK'),
 'th': ParagraphStyle('th', fontName=HEAD, fontSize=9.2, leading=14.2, textColor=INK, wordWrap='CJK'),
}


def paragraph(text, style='body'):
    return Paragraph(escape(str(text)).replace('\n', '<br/>'), styles[style])


def make_table(headers, rows, widths):
    content = [[paragraph(x, 'th') for x in headers]] + [[paragraph(x, 'cell') for x in row] for row in rows]
    item = Table(content, colWidths=widths, hAlign='LEFT', repeatRows=1)
    item.setStyle(TableStyle([
      ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F1F1')), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
      ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
      ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
      ('LINEABOVE', (0, 0), (-1, 0), 0.65, INK), ('LINEBELOW', (0, 0), (-1, 0), 0.45, INK),
      ('LINEBELOW', (0, 1), (-1, -1), 0.25, colors.HexColor('#CFCFCF')),
    ]))
    return item


def timing_row(label, value):
    s = value['summary']
    return [label, str(s['n']), f"{s['mean_seconds']:.3f}", f"{s['min_seconds']:.3f} ~ {s['max_seconds']:.3f}"]


def decorate(canvas, doc):
    width, height = A4
    canvas.setFont(BODY, 8)
    canvas.setFillColor(GRAY)
    if doc.page > 1:
        canvas.drawString(52, height-31, 'LabAgent 技术白皮书')
        canvas.drawRightString(width-52, height-31, '2026-11-27')
    canvas.setStrokeColor(colors.HexColor('#C7C7C7'))
    canvas.setLineWidth(0.35)
    canvas.line(52, 40, width-52, 40)
    canvas.drawString(52, 26, '部署、调用路径与实测记录')
    canvas.drawRightString(width-52, 26, str(doc.page))


def build():
    story = []
    def add(text, style='body'):
        story.append(paragraph(text, style))
    def table(headers, rows, widths):
        story.extend([make_table(headers, rows, widths), Spacer(1, 10)])
    def next_page(title):
        story.append(PageBreak())
        add(title, 'section')

    add('LabAgent 技术白皮书', 'title')
    add('Orion O6 / CIX P1  |  2026年11月27日', 'small')
    add('本文依据板端当前运行代码、进程参数和本轮采样记录编写。系统在本地完成器材识别、仪表设置核对、液晶读数及语言交互；CPU执行流程、语言生成和OCR，NPU执行器材检测、语音网络和仪表视觉编码。当前服务未使用GPU推理。')
    add('1  模型部署', 'section')
    add('参考设备为32 GB内存的Radxa Orion O6，ARM64 Linux，内核6.6.89-3-sky1。各服务常驻，接口仅监听127.0.0.1；Web与模型通过HTTP通信，NPU视觉编码使用用户级Unix socket。')
    table(['模块 / 模型', '执行位置与主要参数', '接口'], [
      ['规划、对话与工具选择\nQwen3-4B Q4_K_M', 'CPU，8个大核线程，KleidiAI；8192上下文，单路请求，关闭思考输出', '8081'],
      ['器材检测\nYOLO26n INT8', 'CIX NPU；960×960输入，CPU完成图像处理、输出解码与NMS', '8035'],
      ['液晶读数\n微调PP-OCR识别器 v3', 'CPU ONNX Runtime，4线程；保留数字、负号和小数点检查', '经8031'],
      ['仪表设置\nQwen3-VL-4B混合路径', 'NPU处理320×320视觉编码，CPU生成受约束短文本；旋钮与插孔分别处理', '8034\nUnix socket'],
      ['通用视觉\nQwen3-VL-4B Q4_K_M', 'CPU语言与视觉投影；FP16 mmproj，4096上下文，8线程', '8032'],
      ['语音输入\nSenseVoiceSmall .cix', 'CIX NPU执行网络，CPU完成解码、声学特征和CTC后处理', '8011'],
      ['Web、流程、检索与记录', 'Python / FastAPI / SQLite；字符n-gram TF-IDF检索', '8001'],
    ], [149, 266, 76])
    add('应用环境为Python 3.11.2，NumPy 2.4.6、ONNX Runtime 1.31.1；NPU/ASR独立环境使用NumPy 1.26.4和libnoe 2.0.0。厂商运行时与板端驱动配套安装。完整Qwen3的语言生成仍在CPU，仪表混合路径只将视觉编码放到NPU。')
    add('本轮重新计算的YOLO、OCR、GGUF和视觉编码器摘要与既有模型清单一致。模型版本、服务参数和依赖版本见runtime.json、final-source-check.json；GitHub的YOLO / OCR附件可继续使用。[1]', 'small')

    next_page('2  异构算力调用逻辑')
    add('2.1  器材检查', 'sub')
    add('浏览器取帧后，经Web 8001和视觉适配器8031调用YOLO 8035。NPU输出万用表、电池、红表笔、黑表笔四类目标；CPU解码、NMS并映射实验清单。预览只更新内存，不逐帧写数据库。缺少器材时等待画面变化；同一会话版本下预览齐全并连续稳定约0.7秒后，才发起正式核验。')
    add('正式器材证据仍使用两次独立观察。首帧齐全后，第二帧沿用本次检查授权，不再重复请求语言模型；证据通过后由规则和事务推进步骤。对“还缺什么”等事实查询，程序可直接根据有效观察生成答复。')
    add('2.2  仪表设置', 'sub')
    add('先用YOLO找到一台清晰的万用表，再分别裁剪旋钮和插孔区域。两张区域图各缩放到320×320，由NPU编码，CPU按限定字符协议输出档位、量程及插孔结果。程序检查两次编码请求计数确实增长，再接受混合后端结果。无法定位、细节过小或回复不完整时保留未知。')
    add('页面在本步骤首次自动检查一次；定位失败可短暂重试，设置未通过后等待用户点击重测。结果绑定本次照片、会话、步骤和版本。当前接口中的“live”名称沿用历史命名，交互实际采用单帧照片核验。')
    add('2.3  按键读数与慢路径', 'sub')
    add('LLM选择读表工具后，页面等待“已接好，开始读数”操作，先显示5秒准备倒计时，再定位LCD并采集单帧。定位未成功时按约0.8秒间隔继续取帧；读数请求使用fast_only，定位和识别各有8秒请求超时。OCR不确定时直接返回未知，不进入较慢的通用VLM回退。')
    add('单帧已辨识数值按计划范围和既有量程、时效条件录入，保留实际符号；零值、负值可被保留并提示核对接触和极性。报告标为“单帧显示屏识别”。旧的双帧采集接口仍保留稳定性校验，不能将两种记录方式混称。普通识别接口在未启用fast_only时仍可回退到CPU VLM。')
    add('2.4  状态与记录', 'sub')
    add('模型负责建议和工具选择，状态机负责提交条件。暂停、切换、刷新或删除后，过期的会话版本不能继续写入结果。SQLite保存测量与审计记录；报告使用已保存、未被替代的数据。相机取景设备与执行模型的O6可以是不同设备。')

    next_page('3  真实性能数据')
    add('2026年11月27日，在上述常驻服务上由单个客户端串行采样。以下均为HTTP完整响应耗时，包含服务端相应处理及本机通信，不含模型加载、浏览器取景、5秒倒计时或实验数据库写入。样本很少，仅报告均值和实际范围。[2]')
    rows = [timing_row('LLM：简短中文', BENCH['llm']['short']),
            timing_row('LLM：带说明的问答', BENCH['llm']['manual']),
            timing_row('LLM：get_status工具', BENCH['llm']['tool']),
            timing_row('YOLO：完整场景图', BENCH['yolo']),
            timing_row('OCR：前置小数点 .419', BENCH['ocr_fast_only']['leading_decimal_419.png']),
            timing_row('OCR：负零 -0.00', BENCH['ocr_fast_only']['negative_zero.png']),
            timing_row('OCR：1.353', BENCH['ocr_fast_only']['reading_1_353.png']),
            timing_row('仪表：旋钮和插孔完整核验', BENCH['meter'])]
    table(['请求', '次数', '均值 / 秒', '实际范围 / 秒'], rows, [211, 48, 96, 136])
    add('LLM条件', 'sub')
    add('三类请求各运行2次，temperature=0、seed=42、最大输出64 token、cache_prompt=false。简短中文、带说明问答和工具请求的输入/输出分别为23/17、91/28、153/16 token。提示词和逐次用量保存在原始记录中；本轮提示词与11月17日的历史对照不同，不能据两张表直接计算升级提速比例。')
    add('视觉条件与结果含义', 'sub')
    add('YOLO对同一张留存场景图预热1次、计时5次；OCR对3张开发诊断裁剪图各计时3次。前置小数点样例因位置冲突被拒收，负零和1.353保留原字符串；这些是功能及延迟检查，不是新采集的独立准确率评测。')
    add(f"仪表完整核验为同一张留存场景图的2次请求，NPU编码计数合计增加{BENCH['meter']['npu_request_delta']}次。响应中的约0.491秒vision_ms只对应最后一次区域编码，不能代替约11秒的整次请求。输入图的SHA-256随记录保存，未在公开数据中嵌入照片。")
    add('已有精度基线', 'sub')
    add('11月19日YOLO在25张同桌面、同相机验证图上的计数为TP=70、FP=0、FN=6，漏检均为电池，recall约0.921。该模型摘要本轮未变，但这仍是特定数据集的历史结果。OCR的81张照片曾用于训练，81/81拟合结果不能作为独立测试准确率。[3]')

    next_page('4  优化经验与当前限制')
    add('减少重复模型调用。器材缺失时持续使用轻量预览，齐全后由规则完成第二帧核验和推进；读表阶段复用有效工具决定，不在定位重试之间再次调用LLM。状态、采样次数和器材缺项等事实答复可由本地记录生成，避免增加模型往返。')
    add('把快速采集与复杂识别分开。按键采集只走定位和OCR快路径，不能确认就结束本轮并提示调整；通用识别另保留VLM回退。这样可以约束等待时间，但代价是快路径遇到模糊图像时会拒收，必须记录拒收而不是填入估计读数。')
    add('裁剪和协议比盲目增大模型更直接。旋钮、插孔分别放大后送入固定输入编码器，用字符语法约束输出；LCD则保留负号、小数点和数字位的像素检查。局部图像缺失的信息无法靠置信度补回，应通过拍摄条件或人工复核解决。')
    add('按实测选择算力。当前LLM使用8个大核和KleidiAI。11月17日的固定“带资料问答”历史对照中，优化CPU平均14.654秒，全层Vulkan平均104.681秒，因此生产路径选择CPU。NPU适合当前已验证的检测、语音和视觉编码图；完整语言模型迁移、通用动态负载调度并未上线。[4]')
    add('明确单帧记录的定义。当前按键入口保存单帧数值，不能描述成经过双帧稳定性验证的电压。兼容接口与部分旧提示词还保留两帧表述，后续应统一文案。测试中也发现了同类遗留：实现已将区域推理超时改为30秒，测试仍断言不超过12秒；发布仓库仅同步该断言，应用实现保持板端原样。')
    add('本次发布复测：本地350项通过，O6临时源码副本346项通过；原始板端测试的7个旧超时断言失败也保留在证据中。测试使用受控模型返回，不能据此推导真实识别准确率。整机重启、长时间并发、功耗和跨场景准确率尚未完成专项测量。')
    add('数据与复现入口', 'sub')
    add('[1] evidence/board_refresh/2026-11-27/runtime.json、final-source-check.json：当前进程参数、模型摘要及设备句柄。\n[2] 同目录model-benchmark.json；复现脚本scripts/benchmark_deployed_models.py。\n[3] evidence/model_exports/2026-11-25/yolo/npu-validation.json；OCR开发记录见evidence/ocr_training/production-reader/。\n[4] evidence/model_acceleration/2026-11-17/llm-benchmark.json：历史CPU/GPU逐次请求。', 'small')
    add('代码与模型下载：https://github.com/Stan0403/cix-labagent\n本次板端快照摘要：' + CHECK['captured_source_tree_sha256'], 'small')
    document = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=52, rightMargin=52, topMargin=53, bottomMargin=53,
                                title='LabAgent 技术白皮书 2026-11-27', author='LabAgent Project',
                                subject='模型部署、异构算力调用、实测数据与优化经验')
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    print(OUT)
