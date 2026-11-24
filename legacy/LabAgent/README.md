# LabAgent · 可运行的实验智能助手

当前实现面向“低压模拟传感器电压测量”九步实验，提供真实的状态机、规则校验、SQLite 持久化、本地知识检索、模型工具接口、浏览器摄像头/图片/录像输入、OCR、语音转文字接入、日志和报告导出。

当前为研究原型。2026-10-27 已在 CIX P1 部署 Qwen3-4B CPU 本地对话、SenseVoice NPU 语音及主服务，并验证实际工具调用；检测、OCR 和 BGE 尚未部署。未配置能力会明确报错。原方案与当前实现的对应关系见 [模型部署说明](docs/MODEL_PLACEMENT.md)，模型选型限定于用户指定的 CIX 26_Q2 库。

## 本机启动（Windows）

项目已安装独立 `.venv`，Python 3.12；不会改动系统的 Python 3.7。

**最简单：双击 `start.cmd`**，保留打开的终端窗口，然后访问下方网址。

在本文件所在目录打开 PowerShell：

```powershell
.\scripts\start.ps1
```

浏览器访问 **http://127.0.0.1:8000**。关闭终端或按 Ctrl+C 停止。

如果脚本执行策略阻止运行，可以不更改全局策略，直接运行：

```powershell
.\.venv\Scripts\python.exe -m labagent
```

新机器首次安装（需 Python 3.11+）：

```powershell
.\scripts\setup.ps1 -Python 'C:\Path\To\Python312\python.exe'
```

Linux / P1 CPU 环境（需系统已安装 Python 3.11+ 和 venv）：

```bash
bash scripts/setup.sh
.venv/bin/python -m labagent
```

安装脚本不会覆盖已有 `.env`。虚拟环境不应复制到另一台电脑，请在目标机器重新创建。

当前 P1 已有独立 `.venv-linux` 主环境和 `.venv-asr` 语音环境，对话推理复用系统自带的 llama-server。板上三个服务的启动与电脑 SSH 隧道见 [本地对话部署说明](docs/LOCAL_LLM.md)，录音步骤见 [本地语音部署说明](docs/LOCAL_ASR.md)，无需重复安装环境。

## 不填 API 时怎么用

1. 点击“新建真实实验”。默认是**规则流程 + 本地原文检索**，不宣称调用了大模型。
2. 每步在现场完成检查后勾选“人工确认”，点击相应操作；再点击“验证并进入下一步”。
3. 测量阶段输入实际读数并确认，进入保存阶段后重新勾选确认并保存，再进入报告阶段。
4. 可随时问“现在做到哪一步了？”“为什么还不能进入下一步？”。输入“下一步”“暂停”“重新测量”“保存测量”“生成报告”可执行受规则约束的明确命令。
5. 使用左侧的三个回放剧本验证正常流程、缺少万用表和未确认断电的分支。回放永远创建独立会话，并在报告注明非真实测量。
6. 下载 Markdown 报告、CSV 测量数据或 JSON 完整日志。数据实际保存在 `data/labagent.sqlite3`。

确认默认 5 分钟过期，过期后需要重新检查，不是软件故障。若连接/接线确认过期，可使用“接线发生变化”重新检查：先现场断电，暂停状态下勾选并“确认恢复”，然后按 SOP 重走准备步骤。

## 最后需要填写哪些配置

打开项目根目录 **`.env`**，密钥只放在本地这个文件，不写进网页或代码。修改后重启服务。

| 需要的能力 | `.env` 中要改的变量 |
|---|---|
| 真实大模型问答/工具调用 | `LABAGENT_CHAT_PROVIDER=api`、`LABAGENT_LLM_BASE_URL`、`LABAGENT_LLM_MODEL`，服务要求认证时再填 `LABAGENT_LLM_API_KEY` |
| VLM 器材识别 | `LABAGENT_VISION_PROVIDER=vlm`、`LABAGENT_VLM_BASE_URL`、`LABAGENT_VLM_MODEL`，可选 `LABAGENT_VLM_API_KEY` |
| 本地自训练检测模型 | `LABAGENT_VISION_PROVIDER=onnx`、`LABAGENT_ONNX_MODEL`、`LABAGENT_ONNX_LABELS`；详见 `models/README.md` |
| VLM 读取显示屏 | `LABAGENT_OCR_PROVIDER=vlm`，复用上述 VLM 服务配置，网页中选择显示屏 ROI |
| 本地 Tesseract OCR | 安装实际 Tesseract 可执行程序，设 `LABAGENT_OCR_PROVIDER=tesseract`、`LABAGENT_TESSERACT_COMMAND`；不需要云端 API |
| 语音输入 | `LABAGENT_ASR_BASE_URL`、`LABAGENT_ASR_MODEL`，可选 `LABAGENT_ASR_API_KEY` |
| 修改实验器材/电压范围 | 修改 `configs/experiment.json`，同步修改 `knowledge/实验指导.md`；正式实验先由教师审核 |
| 导入你们的真实实验手册 | 在“接入状态”中上传 UTF-8 `.md/.txt`；或放到 `data/knowledge/` 后重启 |
| USB 小开发板上报读数 | 运行 `scripts/serial_bridge.py`，协议详见 `docs/CONFIGURATION.md` |

服务地址应是包含 `/v1` 的根地址（按实际服务填写），代码会追加 `/chat/completions` 或 `/audio/transcriptions`。本机服务允许 HTTP，非本机服务要求 HTTPS。模型要实际支持相应的工具调用/图片/音频接口；不是任意模型名都兼容。

本机兼容服务的配置示例（**必须将模型名替换为该服务实际加载的名称**）：

```dotenv
LABAGENT_CHAT_PROVIDER=api
LABAGENT_LLM_BASE_URL=http://127.0.0.1:8080/v1
LABAGENT_LLM_MODEL=your-actual-loaded-model
LABAGENT_LLM_API_KEY=
```

完整接入说明：[docs/CONFIGURATION.md](docs/CONFIGURATION.md)。

## 测试

```powershell
.\scripts\test.ps1
.\.venv\Scripts\ruff.exe check .
```

测试覆盖正常实验、条件阻断、重测、数据来源隔离、超时/未配置模型、HTTP 接口约定、持久化、并发冲突、上传和权限边界。
模型 HTTP 测试使用 MockTransport，是接口契约测试，不是云端服务实测；图片识别集成测试中的受控返回也不是模型准确率。

真实图片的标注评估、验收清单与实机待验证项见 [docs/TESTING.md](docs/TESTING.md)。

## 代码位置

```text
labagent/
  main.py          FastAPI 接口、安全边界、图片/语音/数据导出
  rules.py         九步状态机、安全与时效规则
  agent.py         对话、工具路由、受限制的模型交互
  store.py         SQLite 事务、并发版本校验、操作审计
  providers.py     对话 / VLM / ASR 的真实 HTTP 适配
  perception.py    YOLOv8 ONNX 推理、ROI OCR 和 VLM 视觉
  knowledge.py     本地中英文字符 n-gram TF-IDF 检索
  report.py        Markdown / CSV 报告
  web/             不依赖前端构建工具的 HTML / CSS / JavaScript
configs/           实验定义
knowledge/         随项目分发的示例 SOP
fixtures/          明确标记的测试回放剧本
scripts/           安装、启动、测试、设备接入、实际图片评估
tests/             自动化测试
data/              本机运行数据（不应提交 Git）
```

## 必须保留的边界

- “人工确认”不等于“自动电气验证”；不从灯光、导线颜色或 VLM 判断推断断电。
- 不直接驱动电源、继电器或机械臂。连接变化、暂停、过期确认均需按 SOP 处理。
- 示例 0–3.3 V 范围是可修改的实验约束，不是任意传感器的规格。没有具体型号就没有通用接线保证。
- 摄像头/语音只有用户点击后才开启。配置 VLM/ASR 后会向你填写的服务发送相应数据，请确认权限。
- 默认仅监听 127.0.0.1。不是多租户公网系统；如需远程访问必须设置访问令牌并通过 HTTPS/可信网络部署。
- 当前知识检索为可离线运行的 TF-IDF，不冒充尚未部署的 BGE 向量模型。可在后续测评后替换检索器。
- CPU/GPU/NPU 模型量化、CIX SDK 接入、模型准确率、板上内存和实时帧率都必须实测，不能由软件回放推导。

## 接口参考

HTTP 工具格式按 [官方 Chat Completions 文档](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) 和 [Function calling 文档](https://developers.openai.com/api/docs/guides/function-calling) 核对；这是本地兼容接口适配，不保证所有 OpenAI 模型或第三方服务都支持该接口。
本地检测使用 [ONNX Runtime Python API](https://onnxruntime.ai/docs/api/python/api_summary.html)。
