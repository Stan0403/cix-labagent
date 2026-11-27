# LabAgent · 从实验要求到端侧现场指导

在 Orion O6 本地运行的实验助手。导入实验要求，生成并审核独立执行计划，再结合摄像头、语音、现场确认和测量证据逐步指导。默认提供“万用表测量单节 1.5 V 干电池电压”。流程长度由计划决定。

## 在 O6 上使用

浏览器打开 **http://127.0.0.1:8001**。摄像头和麦克风使用 O6 上的设备。

1. 上传文字 PDF、DOCX、TXT、Markdown，或直接粘贴实验要求。
2. 自动模式优先保留编号步骤；未编号要求由 O6 本地模型提取操作序列。可手动选择拆解方式。
3. 对照原文检查步骤、器材、单位和测量范围，必要时调整；审核后开始真实实验。
4. 进入器材阶段后 Agent 自动拍摄并调用 O6 上的 VLM 检测器材，自动勾选并标框；补齐器材后会再次观察。可以修正误识别，再最终确认。
5. 后续按工作流自动检查档位、插孔和表笔接触；通过后自动读屏。两张不同现场画面读数相差不超过 0.02 V 时自动记录并保存。
6. 按提示移开表笔并关闭万用表；视觉检查通过后自动完成报告，可导出 Markdown、CSV 或完整证据 JSON。

当前版本操作：[自动检查工作流](docs/WORKFLOW_GUIDE.md)。历史演示说明：[比赛演示与使用说明](docs/DEMO_GUIDE.md)。

## 端侧约束和模型

`LABAGENT_LOCAL_ONLY=true` 时所有模型请求只能发往本机回环地址；无云端回退、自动联网补模型或模拟推理。

| 能力 | 模型与实际执行位置 | 独立服务 |
|---|---|---|
| 文档语义拆解、复杂问答、工具调用 | Qwen3-4B Q4_K_M，O6 CPU 大核 / KleidiAI | 127.0.0.1:8081 |
| 语音转文字 | SenseVoiceSmall，CIX NPU；CPU 前后处理 | 127.0.0.1:8011 |
| 器材观察与表笔局部复核 | Qwen3-VL-4B，CIX NPU 视觉编码 + CPU 语言生成 | 适配器 8031，模型 8034 |
| 显示屏候选、档位与接触检查 | Qwen3-VL-4B Q4_K_M + FP16 视觉编码器，O6 CPU | 适配器 8031，模型 8032 |
| 当前步骤、阻断、确认、审计 | Python/FastAPI/SQLite，O6 CPU | 127.0.0.1:8001 |

沿用 O6 已有对话、语音和 Qwen3-VL 语言权重，新增 CIX 官方 NPU 视觉编码权重。模型部署记录见 `docs/QWEN3_VL_4B_DEPLOYMENT.md`，原目录记录见 `docs/CIX_MODEL_SOURCES.json`。模型文件在原项目的 `models/`，新演示目录建立本地链接。许可证、版本和校验清单保留。

2026-11-17 已实测 CPU、Vulkan GPU 和 NPU：对话模型使用优化 CPU 运行时，SenseVoice 使用 NPU，器材检查已切换到 NPU 视觉 + CPU 语言，其他视觉任务保留 CPU。实测数据、验证范围和回退方法见 [模型算力分配](docs/MODEL_ACCELERATION.md)。

器材识别的正式 NPU 路由、固定分辨率和数值兼容性限制见 [NPU 部署记录](docs/NPU_EQUIPMENT.md)。当前整套器材检查仍约需 30 秒，不是实时小模型检测。

TF-IDF 知识检索在本地执行，按当前实验文档隔离检索来源。它不是尚未部署的 BGE。器材观察由 VLM 直接读取完整画面，输出器材名称和位置；颜色、轮廓不会决定器材是否存在。显示屏定位可使用本地图像处理，再由 VLM 读数，定位失败时请求 VLM 定位。档位、接触和收尾检查按固定规则处理 VLM 观察，读数按稳定性规则核验；支持暂停自动检查后人工接管和修正。器材清单保留最终人工提交。

## 部署与管理

本次独立目录：`/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo`。

```bash
cd /home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo
# 管理完整的新演示栈；原项目的 8000 端口保留。
systemctl --user restart labagent-demo-web.service labagent-demo-vlm.service
.venv-app/bin/python scripts/board_runtime.py status
```

服务由当前用户的 systemd 管理，可在用户登录后启动并在异常退出时重启：

```bash
systemctl --user status labagent-demo.target
systemctl --user restart labagent-demo-web.service
journalctl --user -u labagent-demo-web.service -n 50 --no-pager
```

停止新版（保留原项目）：

```bash
.venv-app/bin/python scripts/board_runtime.py rollback
```

主服务使用独立 `.venv-app`；ASR、VLM 复用原项目的独立运行环境。源码、数据库、配置和模型权重分别管理。移到新板需要重新安装各后端依赖，不能复制虚拟环境冒充重新部署。

## 文档与流程边界

- 支持不超过 8 MB、最多 30 页的文字 PDF，以及 DOCX/UTF-8 文本；扫描件暂需先转换为文字。
- 文本最长 40000 字符；未编号文档的模型拆解限制为 12000 字符，长文档建议明确编号主要步骤。
- 每个计划最多 24 步，当前支持一个测量阶段和一个保存阶段，测量阶段可含 1–10 次采样。
- 自动读表仅支持 ±20 V 范围内直流电压；其他测量可以审核单位、范围后人工录入。
- 编号步骤保留原文行号；模型提取的步骤标为待审核建议。模型不能启用计划或自行证明现场安全。
- 会话保存开始时的计划快照；修改计划不改变已有实验。
- 候选图像、模型原始输出、量程、修正值、确认和正式测量相互关联。未确认候选不成为测量。
- 软件演练始终标记为 replay，不能作为新的真实物理实验记录。

## 验证

```bash
.venv-app/bin/python scripts/test_board.py
# 实际板端浏览器 + 实际 VLM，使用明确标记的演练会话：
.venv-app/bin/python scripts/browser_board_smoke.py
```

测试覆盖旧流程兼容、文档导入、计划审核和快照、缺失阻断、量程确认、候选过期/重复/跨会话拒绝、修正追溯、暂停恢复、报告和端侧模型限制。`test_board.py` 在测试时读取板上已有视觉/音频库。

自动化测试和实拍样例只证明对应链路，不能代替你们拿实际电池、相机和麦克风完成现场验收。最终结果与限制记录在 `docs/DEMO_VERIFICATION.md`。

## 主要代码

- `plans.py`：文档读取、受约束计划模型和编号流程编译。
- `demo_api.py`：本地模型拆解、计划审核、现场观察、候选原图。
- `workflow.py`：按计划执行的校验、暂停、重测和确认。
- `agent.py`：即时状态指导、本地检索/模型、受约束工具调用。
- `readings.py`：保留符号、小数点、单位与量程约束的候选解析。
- `store.py` / `report.py`：SQLite 事务、证据、测量、报告。
- `web/demo.*`：文档入口和实验工作台。

原九步传感器流程保留在兼容接口与回归测试中，新版网页默认从文档计划创建实验。

视觉模型的本次部署、局部复核、性能记录与回退方法见 `docs/QWEN3_VL_4B_DEPLOYMENT.md`。
