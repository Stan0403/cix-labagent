# 实验 Agent 使用与部署

## 本次改动

入口支持三种输入：直接输入实验目标、上传实验说明、目标与说明组合。
例如：“测量单节 1.5 V 干电池端电压，独立采集三次并生成报告”。
说明支持 UTF-8 TXT、Markdown、文字 PDF、DOCX，最大 8 MB；扫描 PDF 需要先转成文字。
模型规划正文最多 12000 字符，长的编号说明可以选择按编号解析。
计划是模型建议，需审核；尚未解决的澄清事项会阻止启动。

新实验默认使用 Agent 执行。已有实验记录保持原来的运行方式；可取消“由 Agent 根据现场反馈选择行动”使用兼容流程。

## 页面操作

1. 输入目标或上传说明，可填写补充条件，点击“生成实验计划”。
2. 核对器材、步骤、单位、接收范围、样本数和待澄清事项。需要时补充信息重新生成。
3. 勾选已审核，点击“审核并启动实验 Agent”。首次观察时允许摄像头权限。
4. Agent 选择行动，后端校验后执行，结果返回给 Agent 再次判断。
5. 器材清单仍由现场人员最终提交；需要人工操作时，Agent 会等待。
6. 可以随时问进度或要求暂停。对话可提出采样次数调整，点击明确标注的新采样目标后生效。
7. 目标满足后自动保存、完成报告，可导出 Markdown、CSV 和完整证据 JSON。

## 决策与工具

`TaskAgent` 使用当前目标、步骤、阻塞条件、正式证据、最近工具反馈和最新观察摘要决定一个行动。
默认使用 JSON Schema 约束输出，支持切换为原生工具调用。选择仍来自 LLM，程序只限制允许的行动及参数；
模型不可用、输出无效或超过条件时明确报错，不使用固定规则冒充模型决策。

允许的行动包括器材识别（YOLO fast / VLM semantic）、当前步骤检查、读表、检索说明、
询问用户、返回已执行的核对步骤、推进、保存和报告。
每个决定绑定会话、步骤和 revision，执行后不可重复产生副作用；暂停、修改状态或票据过期会拒绝旧行动。
模型和观察的调用、参数、结果摘要及计划修改都写入审计日志。

连续 YOLO 预览独立运行，摘要在短时内存中维护，不逐帧保存图像或刷写实验日志。
场景稳定变化、现场确认、工具返回或用户操作会唤醒下一轮决策。
等待人工确认期间缓存决定，避免不断请求模型。暂停和事实进度问答在规划模型忙时仍可响应。

对话和决策复用已有对话 LLM；视觉与语音继续使用现有独立模型服务。
器材 fast 路径保持 YOLO26n / CIX NPU；semantic 是新增 VLM 复核路径。
插孔、档位、接触和读数仍使用已接入的视觉服务；没有声称已完成 OpenCV 专用检查。

## 当前范围

当前一个实验支持一个测量阶段、一种测量单位，1–10 次采样。电压自动读数限 ±20 V。
其他测量采用人工适配器；多个电池 A/B 自动身份分组尚未实现，应拆分独立实验或明确人工记录。
采样计划调整保留已有读数，不自动删除离群值，不代替现场确认。
局部重检保留测量历史，清除目标检查及其后续确认。
模型的画面检查是软件观察，无法证明实际电气接触或设备安全。

## 配置

沿用现有 `.env` 的 `LABAGENT_LLM_BASE_URL`、`LABAGENT_LLM_MODEL` 和按需设置的 `LABAGENT_LLM_API_KEY`。
板上已有本地服务为 `http://127.0.0.1:8081/v1`，模型标识 `qwen3-4b-local`。
文档解析需要 pypdf，已加入项目依赖；本地虚拟环境已安装，板上原应用环境也已有。

可选配置见 `.env.example`：

```dotenv
LABAGENT_AGENT_DECISION_FORMAT=json
LABAGENT_AGENT_MAX_ACTIONS=12
LABAGENT_AGENT_TICKET_SECONDS=120
LABAGENT_AGENT_SEMANTIC_EQUIPMENT=true
```

板端 llama.cpp 需支持 JSON Schema；已有启动配置使用 `--jinja` 并关闭 thinking。
已通过板端 `scripts/verify_agent_models.py` 的真实模型兼容性验证。
本次目标规划约 17 秒，Word 说明规划约 23 秒，单次行动决策约 17–30 秒。
连续 YOLO 预览独立运行；决策、通用 VLM 检查和读表仍需等待模型响应。

## 验证记录

- 本地和开发板全量 Python 测试：各 203 项通过。含目标和四种文档输入、完整 Agent 回放、状态版本冲突、
  暂停、错误工具、重复请求、服务失败、待澄清计划及采样调整。
- 两组 Node 生命周期测试通过：连续检测及 Agent 请求/执行/暂停/场景变化。
- 浏览器端到端：输入目标、审核计划、人工确认器材、三次测量、保存、报告、上传 Word、手机布局；无浏览器错误。
- 浏览器和多数接口测试使用明确标记的测试模型/相机，数据写入隔离的 replay 数据库。
  测试截图和报告在 `test-results/agent-browser`，不代表真实硬件测量。
- 真实本地 Qwen3-4B：目标规划及三个状态的工具选择通过。未观察器材时选择 YOLO，
  已观察完整器材时等待人工提交，测量条件满足时选择读表。
- 真实 Word 上传、模型规划、实际 YOLO26n CIX NPU 工具调用及后续模型判断通过。
  使用已有摄像头采集图，识别万用表、电池、红表笔和黑表笔；工具 HTTP 请求约 0.38 秒。
  这是一次采集图集成验证，未执行真实接线、测量或验证各种摆放条件下的准确率。
- 2026-11-20 已激活正式服务；Web、视觉适配器、YOLO NPU、对话 LLM 健康检查通过。
  实测报告保存在 `test-results/agent-board/`。

## 板端部署

目标目录：`/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo`。
本次读取的代码基线备份在板端 `.tmp/agent-flow.7rvAjW/source.tar.gz`。
2026-11-20 已核验代码基线并部署；替换 20 个更新清单文件。
更新包不包含 `.env`、数据库、私钥、模型或虚拟环境。
本次代码回滚备份为目标目录内 `.runtime/agent-update-20261120-135747`。
本次只重启 Web 和视觉适配器，保留已有模型文件、用户数据及其余服务配置。
主应用虚拟环境补装 `opencv-python-headless==4.11.0.86`，未改 NumPy 和 NPU 专用环境。

解压更新包至独立候选目录，用已有 `.venv-app/bin/python` 执行隔离测试和真实模型预检：

```bash
cd /path/to/candidate
/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo/.venv-app/bin/python -m pytest -q
/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo/.venv-app/bin/python scripts/verify_agent_models.py --output .tmp/model-validation
```

预检通过后，部署脚本验证原文件哈希、创建备份，只替换更新清单中的代码并重启 Web 与视觉适配器。
原 YOLO NPU、通用 VLM、对话及语音模型服务保持现有配置。
部署后刷新 `http://localhost:8001`；它需要电脑到开发板的原有 SSH 转发。

当前开发板地址为 `172.20.10.4`，电脑已建立 `localhost:8001` 转发。
刷新页面后新建实验以使用 Agent；已有会话仍保持创建时的执行方式。
若 SSH 转发关闭，在电脑重新建立（密码为开发板账户密码）：

```powershell
ssh -N -L 8001:127.0.0.1:8001 radxa@172.20.10.4
```

真实模型预检通过后，在候选目录执行激活（将 `/path/to/candidate` 换成实际路径）：

```bash
/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo/.venv-app/bin/python scripts/deploy_agent_update.py --app /home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo --candidate /path/to/candidate --model-report .tmp/model-validation/model-report.json --activate
```

去掉 `--activate` 只检查候选文件和板端基线，不改生产文件。
检测到团队后续修改时脚本拒绝覆盖，需先合并；健康检查失败时恢复本次代码备份。
正式启用后若已创建新 Agent 会话，后续手动回滚应兼容新增的 automation、input_mode 和 questions 字段，不能只覆盖旧源码。
`AGENT_MAX_ACTIONS` 限制连续无采样进展的尝试，成功采集有效测量后重新计数。

## 2026-11-22 决策延迟优化

保留每轮由 LLM 选择一个行动及后端票据、revision、暂停和证据有效期检查。
本轮没有合并多次采样行动，没有替换模型，也没有迁移 GPU/NPU 后端。

- 提示词保留当前步骤必要条件；用已核验样本数、保存数及最近值代替重复的全部测量列表。
- `measurement_progress.recorded` 是程序已核验的样本数，不是等待人工确认的候选数。
  采样尚未满足时模型应先选择新画面读表，候选由原有独立画面稳定性规则核验。
- 删除模型输入中的逐帧时间戳和重复状态标志；时间有效性仍在后端逐次检查。
- 将工具目录和实验静态信息置于变化字段之前，以便 llama.cpp 复用相同前缀的 KV 缓存。
  llama.cpp 原本已默认启用缓存，优化效果必须以实际 `cache_n` 和耗时核验。
- 决策理由要求简短；默认 `LABAGENT_AGENT_MAX_TOKENS=192`，允许 96–384。
  这是输出上限，不是每次固定生成量。截断的决定会报错，不会执行工具。
  普通对话和文档规划的输出预算保持原设置。
- `agent.model_decision` 审计事件新增 `performance`：HTTP 耗时、提示词字符数、输出上限、
  token 用量和服务提供的 `timings`。字段缺失时不声称命中缓存。

对照脚本 `scripts/benchmark_agent_decisions.py` 使用隔离的 replay 数据库和合成状态，
调用真实本地模型但不执行实物测量、不改正式实验记录。对照未观察器材、待人工提交、
首次/第二帧读表、推进、保存和报告七种状态。每个状态的工具选择也要通过，不能只比较速度。
`--reset-prefix` 在被测请求中关闭提示词缓存，并用返回的 `cache_n=0` 确认未复用前缀；
仅插入无关请求不能可靠清掉 llama.cpp 的 RAM 缓存。`--format tools` 可核验原生工具调用路径。

```bash
.venv-app/bin/python scripts/benchmark_agent_decisions.py \
  --source "$PWD" --output /tmp/agent-decision-check --variant current
```

首次请求、问答插入、步骤切换和相同测量步骤的连续决策具有不同缓存条件，应分别报告。
本次优化不代表通用视觉检查、显示屏识别或整个实验达到实时运行。

本地和板端 Python 回归各 183 项通过；七种 JSON 行动状态和两种原生工具调用状态通过。
下表为真实同板模型调用、隔离合成状态的单次对照，单位秒；不包含视觉工具执行和人工操作。

| 状态 | 原版 | 优化后 |
|---|---:|---:|
| 尚未观察器材 | 18.869 | 12.478 |
| 等待最终提交器材 | 17.696 | 14.483 |
| 首次请求读表 | 22.355 | 12.859 |
| 同一测量步骤继续取第二帧 | 13.531 | 2.811 |
| 本步完成后推进 | 17.044 | 10.197 |
| 完成报告 | 20.702 | 9.925 |

六项前后选择相同工具的状态，平均 18.37 → 10.46 秒，降低约 43.1%。
保存状态的旧版选择了 recheck，未计入上述均值；优化版正确选择 save_measurement，耗时 12.385 秒。
第二帧决策的新处理输入从 248 token 降到 32 token，是实际缓存复用收益，不是结果缓存跳过 LLM。
这些小样本不能代表全面决策准确率；首次请求、插入问答和场景变化仍会增加耗时。
关闭提示词缓存、双方均确认 `cache_n=0` 的读表决策对照为 34.062 → 23.314 秒。
因此约 2.8 秒是连续同一步骤复用前缀的结果，不是所有决策的固定响应时间。

本次有线直连为 `192.168.123.2`，NetworkManager 独立配置名 `codex-o6-direct-20261122`。
原连接与 Wi-Fi 配置保留；该直连配置未设为开机自动连接。SSH 保持打开。
