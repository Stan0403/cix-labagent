# 配置与设备接入

## 1. 环境与文件

实际配置：项目根 `.env`。模板：`.env.example`。设置加载顺序：环境变量覆盖 `.env`。
修改后重启服务。密钥不返回前端、不写入操作日志；`.gitignore` 排除 `.env`、数据和权重。

`LABAGENT_DATA_DIR` 相对项目目录解析，默认 `data/`，含 SQLite、导入知识库、已处理图片和 OCR 临时目录。
实验图片在模型处理成功后保存为 SHA-256 文件名。云端 VLM 会看到图像内容，不能因为地址填在本地配置就认为数据留在本机。
`LABAGENT_HTTP_TIMEOUT` 默认 45 秒。模型调用失败会提示错误，不自动改用假结果。

## 2. 大模型与 Agent

当前 P1 已切换为本地 Qwen3-4B 服务，`LABAGENT_LLM_BASE_URL=http://127.0.0.1:8080/v1`、`LABAGENT_LLM_MODEL=qwen3-4b-local`、Key 留空，请求超时为 180 秒。完整启动和验收见 [LOCAL_LLM.md](LOCAL_LLM.md)。`CHAT_PROVIDER=api` 在这里指本机 HTTP 接口。

设置 `LABAGENT_CHAT_PROVIDER=api`，填写 `LABAGENT_LLM_BASE_URL`、`LABAGENT_LLM_MODEL`，需要时填写 `LABAGENT_LLM_API_KEY`。
支持 `POST {BASE_URL}/chat/completions`：请求含 model、messages、tools、tool_choice；响应读取 choices[0].message。
工具调用需返回合法的 function 名、JSON arguments 与 tool_call_id。未实现 Responses API，不要将仅支持 Responses 的模型误填成兼容服务。

规则模式不需要大模型。精确命令列表见 `labagent/agent.py` 的 `requested_action`。命令匹配采用允许列表，避免用户询问“为什么不能下一步”被当成推进指令。
模型可以读取状态、检索手册；不可创建物理安全确认。工具参数经校验，未知工具被拒绝，工具轮数有上限。
自由表达的操作请求可由模型提出建议，界面显示批准按钮；没有匹配明确命令时绝不直接执行。建议绑定提出时的状态修订号，状态改变后旧建议失效。
对新的执行需求应扩展用户授权/确认机制与回归测试，不应简单放开模型的任意状态写入权限。

## 3. 器材检测

### VLM

设置 `LABAGENT_VISION_PROVIDER=vlm` 和三个 VLM 字段（BASE_URL、MODEL、可选 API_KEY）。
服务必须接受 Chat Completions 的 image_url 内容，含 data:image/jpeg;base64 图片。
VLM 器材输出为 `{"objects":[{"label":"sensor","confidence":0.95}]}`；只允许实验配置中的四类标签。
模型自报置信度不是校准概率，也不能证明电气安全。

### ONNX

设置 `LABAGENT_VISION_PROVIDER=onnx`，填写权重文件与类别 JSON 路径。相对路径从项目根解析。
需要本实验器材的标注与训练，普通 COCO 模型无 sensor/multimeter 等类别。
只支持 float32 输入 `[1,3,S,S]`，输出 `[1,4+C,N]` 的 YOLOv8 detect。预处理为 RGB/255、等比缩放与 114 填充；后处理逐类别 NMS。
`LABAGENT_ONNX_SIZE` 必须与模型一致。`LABAGENT_ONNX_PROVIDERS` 必须是本机实际可用提供程序，默认 CPU。
当前没有 CIX NPU 后端；添加实际 NPU 推理可在 `Perception.detect()` 接口后新增实现，不能把任意 provider 名写进配置就当作支持。

## 4. 显示屏 OCR

用户先在画面拖动选择显示屏，坐标为归一化 `x,y,width,height`；也可用 `LABAGENT_METER_ROI` 配置固定机位。
不从整幅实验图中抽取任意数字，避免把印字、量程或传感器型号当作读数。

- `LABAGENT_OCR_PROVIDER=vlm`：复用 VLM 配置，需实际视觉服务。
- `LABAGENT_OCR_PROVIDER=tesseract`：先安装系统 Tesseract，填写 `LABAGENT_TESSERACT_COMMAND` 的可执行路径。虚拟环境不包含这个系统可执行程序。
- `LABAGENT_OCR_PROVIDER=cix`：使用模型库 PP-OCRv4，在板上运行 `bash scripts/start_local_ocr.sh`，并设置 `LABAGENT_OCR_BASE_URL=http://127.0.0.1:8020`。识别服务仅在本机监听。
- `disabled`：清晰显示未配置，人工读取并输入。

OCR 无法确定小数点或单位时拒绝记录；对于不显示单位的万用表，可以逐次现场确认直流档位后按其单位转换。置信度过低或超出实验范围时不创建测量。候选测量仍需进入保存阶段核对。
PP-OCRv4 为通用文字识别模型，万用表七段数字、小数点和单位的准确率需要用真实设备验证；模型置信度未经校准。缺失单位、含多个数字或格式不明确时不会自动推断，需人工输入并核对。

## 5. 语音

填写 `LABAGENT_ASR_BASE_URL`、`LABAGENT_ASR_MODEL`、可选 `LABAGENT_ASR_API_KEY`。服务接收 `/audio/transcriptions` multipart file/model，返回 `{"text":"..."}`。
网页最长录音 60 秒，上传不超过 16 MB。转录先进入文本框，由用户核对并发送，不会自动执行听错的命令。
P1 上新增了 CIX SenseVoice 本地服务，独立环境、启动和配置见 [LOCAL_ASR.md](LOCAL_ASR.md)。使用本机服务无需 Key；不要把本地 ASR 和外部对话 LLM 的运行位置混淆。

## 6. USB 小开发板（可选）

设备固件串口每行输出一段 JSON，电压应为已经按实际 ADC、分压电路和参考电压校准的测量值：

```json
{"voltage":1.25,"unit":"V"}
```

进入网页第 7 步“读取传感器输出”，取得会话完整 ID（完整日志、API 或地址请求中可见），运行：

```powershell
.\.venv\Scripts\python.exe scripts\serial_bridge.py --port COM3 --session YOUR_SESSION_ID
```

Linux 将端口改为 `/dev/ttyACM0` 等实际端口，确保串口权限。
桥接器仅读取数据，不控制供电。不使用收到的“power_off”字符串证明断电，不猜测 ADC 转换系数。
没有确定 MCU、引脚、分压与 SDK 前，不能提供通用安全固件；此处没有附带假固件。请在设备型号确定后按原理图编写固件并验证。

## 7. 实验范围与迁移 P1

`configs/experiment.json` 定义九步流程、器材标签、范围和说明。当前规则实现绑定九步低压实验，新增完全不同实验时需同步扩展 `rules.py` 和测试，不是修改任意步骤数就可用。
配置变更不要沿用已经运行一半的会话，重新创建实验，以免同一个会话前后规则不同。
项目已按 Python 包分层，P1 可先迁移 CPU 版本。安装 ARM64 对应 Python 依赖后运行测试，再部署实际本地模型服务。
NPU 需额外取得主办方实际 SDK、支持的模型格式/算子、量化工具和设备，验证后才能替换推理后端。

## 8. 本地 API

主要路径：`/api/health`、`/api/experiment`、`/api/sessions`、`/api/sessions/{id}/actions`、`/chat`、`/image`、`/sensor`、`/export`，以及 `/api/knowledge/search`、`/api/replays`。
`GET /openapi.json` 提供机器可读 schema。接口 POST 需 Content-Length；HTTP 客户端一般会自动设置。
多页面操作使用 expected_revision，状态发生变化返回 409，避免过期图像或重复点击覆盖最新状态。

如果设置 `LABAGENT_ACCESS_TOKEN`，API 请求使用 `Authorization: Bearer ...`；网页在“接入状态”里填写访问令牌，令牌只保存在该标签页 sessionStorage，不是模型 Key。
默认本机单用户。不是用于公开互联网的认证/权限系统；不提供用户管理、租户隔离、审计防篡改或安全认证。
