# CIX LabAgent - 本地多模态实验助手

代码快照：2026-11-24。正式应用源自 `LabAgent-demo`，包含当前本地改动；逐文件来源见 [SOURCE_SNAPSHOT.json](SOURCE_SNAPSHOT.json)。旧版保留在 `legacy/LabAgent/`。本包不包含正式实验数据库、原始拍摄批次、密钥或虚拟环境；保留3张必要的LCD诊断裁剪测试图。

系统用规则与 SQLite 事务控制实验步骤，模型提出计划、识别建议和工具调用。当前默认电池实验为阅读、器材、仪表、连接并读表、保存、报告六步；负数/负零/零值不计为正向电池的正式样本。读表保留独立采集编号、至少 1 秒间隔、同量程及 0.02 V 稳定性检查。具体规则以源代码和测试为准。

**GitHub 仓库**：[Stan0403/cix-labagent](https://github.com/Stan0403/cix-labagent)，私有仓库，通过 Git 与 GitHub CLI 发布，无需 Codex 插件。[发布附件](https://github.com/Stan0403/cix-labagent/releases/tag/v0.1.0-delivery)包含源码包、PDF 和 OCR 模型包。

```bash
gh repo clone Stan0403/cix-labagent
cd cix-labagent
python quickstart.py
```

私有仓库需要先通过 `gh auth login` 登录获授权的 GitHub 账号。后续发布工具见 [deploy/publish_github.ps1](deploy/publish_github.ps1)。

**2026-11-25 模型补齐**：已从 O6 导出并核验正式 YOLO NPU `.cix` 和 OCR `.onnx`。详见 [模型工件与下载](docs/MODEL_ARTIFACTS.md)。在当前仓库运行 `python deploy/fetch_models.py` 可从同仓库 Release 下载两种模型并逐文件校验。

## 一键启动

环境：Windows/Linux/macOS、Python 3.11 或 3.12、可访问 Python 包源；浏览器支持相机 API。首次安装后续启动复用隔离环境。

```powershell
python quickstart.py
```

Linux/macOS：

```bash
python3 quickstart.py
```

打开 **http://127.0.0.1:8001**，按 Ctrl+C 停止。脚本创建 `.venv`，安装锁定版本，不改写已有 `.env`。端口冲突可用 `python quickstart.py --port 18001`。只安装用 `--install-only`。

新机器默认使用规则与人工核验，可创建实验、人工录入真实读数、保存、恢复/删除会话和导出图文报告。它不会假装已加载大模型或 NPU；无人值守的自动 Agent 需要下面的模型服务。回放仅为软件演练，不能当作真实测量。

## Docker 一键部署

需 Docker Engine/Desktop 与 Compose。设置访问令牌后执行；容器仅映射本机端口，数据使用命名卷。

```powershell
$env:LABAGENT_ACCESS_TOKEN = [guid]::NewGuid().ToString('N')
Write-Output $env:LABAGENT_ACCESS_TOKEN
docker compose up -d --build
```

```bash
export LABAGENT_ACCESS_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf '%s\n' "$LABAGENT_ACCESS_TOKEN"
docker compose up -d --build
```

打开同一地址并按页面提示输入令牌。查看日志用 `docker compose logs -f web`；停止用 `docker compose down`，不删除数据卷。该容器是 CPU Web/规则版，未封装厂商 NPU 驱动。本次交付尚未实测容器构建。

## O6 完整异构部署

实际参考硬件为 **Radxa Orion O6 / CIX P1，32 GB，ARM64 Linux，内核 6.6.89-3-sky1**。NPU 需要与板卡镜像匹配的 `/dev/aipu`、libnoe/NOE_Engine；不能从普通 PyPI 环境推导其可用性。完整步骤见 [O6 部署与模型工件](docs/REPRODUCIBLE_DEPLOYMENT.md)。准备好权重、锁定工件和独立运行时后，一条命令安装并启动用户服务栈：

```bash
.venv/bin/python deploy/o6_stack.py --apply
.venv/bin/python deploy/check_stack.py --wait 180
```

安装器先检查所有工件摘要、运行时及端口，再生成 `cix-labagent-*` 服务。已有服务占用端口时直接报错；不会接管正在运行的原项目。`--render-only` 仅生成供审阅的配置，不代表硬件部署通过。

| 能力 | 当前实际执行位置 | 端口 |
| --- | --- | --- |
| Web、流程、TF-IDF 检索、SQLite、报告 | CPU | 8001 |
| Qwen3-4B Q4_K_M 规划/对话/决策 | CPU，大核 + KleidiAI | 8081 |
| YOLO26n INT8 四类器材 | CIX NPU，CPU 前后处理 | 8035 |
| PP-OCR 液晶 v3 | **CPU ONNX Runtime**，不确定时回退 VLM | 经 8031 |
| Qwen3-VL 通用视觉 | CPU | 8032 |
| 仪表设置短回复 | NPU 视觉编码 + CPU 文本 | 8034 + Unix socket |
| SenseVoiceSmall | NPU 网络 + CPU 音频前后处理 | 8011 |

8031 适配器按任务静态路由，返回真实 `backend`；`ocr_provider=mnn` 是历史协议名，不能用于判断硬件。当前仪表页以 `docs/LIVE_METER.md` 的**单帧拍照核验**实现为准，不能按早期持续检查文档描述为逐帧推理。完整 Qwen3 的 NPU 首层实验留在 `research/npu_llm/`，尚未接管正式语言生成。

## 模型工件与依赖

- [requirements.lock.txt](requirements.lock.txt)：便携 Web 的精确包版本；Python、操作系统及轮子架构仍需匹配。锁文件不等于逐字节容器镜像锁。
- [requirements-dev.lock.txt](requirements-dev.lock.txt)：本次回归环境的测试/CPU视觉版本。现有代码在 NumPy 1.26.4 上实测；不要同时安装声明为 NumPy 2 的旧 `.[vision]` extra。
- [deploy/model_manifest.json](deploy/model_manifest.json)：模型角色、来源和已核验 SHA-256。没有证据的摘要标为 null，完整 O6 部署必须再提供 `ARTIFACT_LOCK.json`。
- YOLO NPU 权重和微调 OCR v3 已作为本仓库 Release 附件分发。`python deploy/fetch_models.py` 自动下载并验证；单独核验用 `--verify-only`。来源与包摘要见 [deploy/board_model_artifacts.json](deploy/board_model_artifacts.json)。
- **完整 VLM/GGUF 和 ASR 工件仍需另行准备**。不能用通用 COCO 模型代替实验四类 YOLO；本次未在板端该模型目录找到原始 FP32 ONNX/训练 PT。完整部署所需工件和路径调整见部署文档。
- CIX 厂商 wheel、驱动及大权重不提交 Git。训练脚本、C++ 视觉桥、构建脚本与第三方声明已收录；真实训练照片不随源码分发，第三轮相同数据重训需原采集批次。

## 真实性能记录

| 项目 | 实测结果 | 条件和解释 |
| --- | --- | --- |
| CPU LLM：短请求 / 带资料 / 工具 | 4.576→2.705 / 23.187→14.654 / 7.660→4.398 秒 | 2026-11-17，每类每配置 2 轮，HTTP 完整响应；固定提示、最大 96 token，非整套实验速度 |
| Vulkan 同三类请求 | 12.156 / 104.681 / 49.023 秒 | 实际卸载，较慢，未进入正式路径 |
| OCR 定位+识别 | 中位 0.294 秒，P90 0.520 秒 | 81 张参与过训练的照片；78 快读、3 回退；库调用，不含网页/HTTP，非独立准确率 |
| 仪表 NPU 视觉+CPU文本 | 3.657 / 3.610 秒 | 两张留存照片的全链路；非现场准确率或逐帧速率 |
| YOLO | 25.85 ms 纯 NPU，69.37 ms 含前后处理 | 25 张同场景验证图；11月25日已补入板端原始 [npu-validation.json](evidence/model_exports/2026-11-25/yolo/npu-validation.json)，不代表独立泛化性能 |

原始 JSON 位于 [evidence](evidence/)，汇总可用 `python scripts/summarize_evidence.py` 重算。完整数据条件、失败路线和优化经验见 [PDF 技术白皮书](docs/CIX-LabAgent-Technical-Whitepaper.pdf)。不以训练拟合、回放 fixture、服务健康或单算子提速声称独立准确率或整模型加速。

## 测试与复现

```bash
python -m pip install -r requirements-dev.lock.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m pytest --junitxml=test-results.xml
python -m compileall -q labagent deploy quickstart.py
python scripts/summarize_evidence.py
```

2026-11-24 当前本地源码已有 **344 项通过，0 失败、0 错误、0 跳过**；回执在 `evidence/delivery/`。不继承系统包的全新 Python 3.12.7 环境也完成344项回归，依赖检查和一键安装/启动均通过，网页及健康接口返回200。[GitHub Ubuntu / Python 3.11 回归](https://github.com/Stan0403/cix-labagent/actions/runs/45421123635)也已通过。O6硬件全新安装、Docker构建和整机重启尚未实测。测试包含 HTTP/模型 fixture；硬件性能来自另附板端记录。GitHub Actions 运行软件回归，不提供 O6 硬件保证。

## 目录

```text
labagent/          当前正式应用、视觉/ASR适配、状态机和网页
configs/           实验定义与默认电池计划
knowledge/         示例实验手册
fixtures/, tests/  软件演练与自动化回归
scripts/           模型服务、采集、验证和工具
deploy/            模型清单、校验/导出、可迁移服务与GitHub发布
docs/              当前部署说明、技术白皮书、历史专项文档
evidence/          真机性能原始JSON和本次交付回执
research/          OCR训练、NPU首层实验、早期器材训练准备
maintenance/       原板端维护/测速源代码（包含旧路径，使用前审阅）
legacy/LabAgent/   原始版本，便于追溯，不是默认运行入口
```

`docs/CURRENT_STATE.md` 和本 README 是当前状态入口；早期专项说明保留日期，其中的“当前”只指当时。维护脚本与 `scripts/board_runtime.py` 是历史部署工具，新的复现使用 `deploy/`。

## 发布到 GitHub（无需插件）

安装 [GitHub CLI](https://cli.github.com/)，执行 `gh auth login --hostname github.com --web` 完成官方浏览器授权。随后在本目录运行：

```powershell
.\deploy\publish_github.ps1 -Name cix-labagent
```

脚本初始化本目录 Git、创建一次提交、创建私有仓库并推送，成功后打印真实链接。等价命令和 Release 上传方式见 [发布说明](docs/GITHUB_PUBLISH.md)。不要把模型权重、数据库和 `.env` 加进普通 Git 提交。

## 数据、许可与边界

页面相机/麦克风取自打开浏览器的设备。真实记录在 `data/`，删除会话需要明确确认；共享图片不随会话批量清理。图像不能证明断电、表笔电气接触或无短接。默认仅本机访问，远程建议 SSH 转发；这不是多租户公网服务。

原始项目未附统一开源许可证，本交付不替权利人授予新许可证。公开分发前确定自有代码授权；第三方与模型许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
