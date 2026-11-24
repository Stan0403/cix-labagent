# 本次交付验证记录

## 2026-10-27 P1 本地对话部署

已按 CIX 26_Q2 选型部署 Qwen3-4B，采用作者官方现成 Q4_K_M GGUF；2,497,280,256 字节和 SHA-256 均校验通过。复用板上 llama-server 5929，最终使用 CPU，关闭思考模式、启用工具模板。独立六项实际模型检查全部通过：中文回答、两个工具协议往返、Agent 手册问答、暂停批准流程、物理确认请求不改写事实。

板上 `.env` 已改为本机模型地址和别名，LLM Key 留空，超时调整为 180 秒；其他配置保留，旧配置安全备份。主服务已重启；具体配置、性能、来源及 HTTP 验收记录见 [LOCAL_LLM.md](LOCAL_LLM.md)。原有语音继续在 NPU 运行，视觉/OCR/BGE 仍未部署。

## 2026-10-27 P1 本地语音增量

已在 P1 创建独立 `.venv-linux` 和 `.venv-asr`，主服务与 CIX SenseVoice NPU 语音服务均已运行。权重实际下载并校验，中文/英文样例、主服务 MP3 转写代理、WebM/Opus、超过 12 秒的分段 WAV 均执行了真实推理；无效音频明确报错，SSH 断开重连后两个服务均正常。WebM 与分段测试有错字，不能把链路验证当作识别准确率验收。详细证据、限制与运行方式见 [LOCAL_ASR.md](LOCAL_ASR.md)。

Windows 回归测试更新为 119 项通过、Ruff 通过；新增语音前后处理及服务错误分支测试。实际 NPU 路径由板端样例另行验证，不以受控模型测试代替。外部 LLM 的配置已保留，本次未调用真实对话 API；视觉/OCR、实际麦克风和传感器仍待验收。

下文保留 2026-10-21 初始 Windows 交付记录，未接入项描述以当时状态为准。

日期：2026-10-21（Asia/Shanghai）。以下仅描述实际执行结果，不代表尚未接入的模型或设备能力。

## 环境

- 项目独立虚拟环境：`LabAgent/.venv`，Python 3.12.14。
- 系统 Python 3.7 未修改。
- 已安装 Web 后端、测试、ONNX Runtime、OpenCV 与 USB 串口依赖。
- 精确版本记录：`requirements-lock.txt`；`pip check` 通过。
- ONNX Runtime 1.31.0，可用提供程序包括 CPUExecutionProvider、AzureExecutionProvider；默认使用 CPU。
- `.env` 已创建，所有真实模型服务地址、名称与密钥留空。

## 已通过

| 检查 | 结果 |
|---|---|
| Python 自动化测试 | 106 项通过 |
| Python 语句覆盖率 | 90%（四舍五入，包含启动模块未覆盖部分） |
| Ruff 静态检查 | 通过 |
| Python compileall | 通过 |
| 前端和浏览器测试脚本语法检查 | 通过 |
| 浏览器新建/阻断/确认/问答/刷新恢复 | 通过 |
| 浏览器回放/报告下载/重测 | 通过 |
| 390 像素手机宽度 | 无横向溢出 |
| 浏览器页面脚本错误 | 未发现 |
| Uvicorn 启动 | 已确认应用启动完成，测试后停止 |

浏览器截图：`test-results/browser-live.png`、`browser-replay.png`、`browser-mobile.png`。
JUnit 结果：`test-results/junit.xml`。这些文件为本次执行产物，不提交 Git。

## 测试边界

当前沙箱的跨进程 localhost 访问受限，因此浏览器测试通过标准输入/输出连接真实 FastAPI TestClient 和 SQLite，未模拟业务接口或测量结果。
该方式测试了实际网页与后端集成，但不声称已验证浏览器至 Uvicorn 的真实网络通路；用户需要在自己的本机双击 start.cmd 启动后访问。

HTTP 模型适配器使用受控响应/MockTransport 测试协议、错误和工具约束。ONNX 检测后处理测试使用受控张量，Tesseract 使用受控进程返回；不把这些测试当作训练模型、OCR 或 API 服务的真实效果。

测试工具库 Starlette 对 httpx TestClient 有一条弃用提示，不影响本次全部测试通过；后续升级开发测试依赖时可迁移其推荐客户端。

## 尚未验证

- 真实大模型、VLM 和 ASR 服务（未填写地址/模型/密钥）。
- 实际摄像头、麦克风、万用表与传感器电路。
- 自训练器材检测权重、实际显示屏 OCR 准确率。
- 系统 Tesseract 可执行程序（未安装；可使用 VLM 替代）。
- MCU 具体固件、ADC 校准、实际 USB 串口。
- CIX P1 的驱动、NPU SDK、模型量化转换、内存/速度/温度。

下一步先按 README 的配置表接入实际模型服务，在真实设备到位后按 TESTING.md 补验收；不能只填 API Key 就宣称完成了硬件验证。
