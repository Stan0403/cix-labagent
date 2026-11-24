# P1 本地对话模型

2026-10-27：开发板已部署 Qwen3-4B，并将 LabAgent 对话地址切换到板内服务。语音仍由 SenseVoice 在 NPU 上执行；视觉、OCR 和 BGE 尚未部署。

## 模型来源与文件

选型来自用户指定的 `cix/ai_model_hub_26_Q2`，条目为 `models/Generative_AI/LLM/Qwen3-4B.md`。该目录提供部署说明，权重按模型作者的官方来源取得。

本次采用作者官方现成的 **Q4_K_M GGUF**，没有自行重新量化。CIX 文档中的 Q4_0 是另一种量化配置，其体积和测速不能直接套用于本次部署。

- 板上项目：`/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent`。
- 权重：`models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf`。
- 文件大小：2,497,280,256 字节，约 2.50 GB；运行内存还包括缓存和工作区。
- 官方权重仓库：`Qwen/Qwen3-4B-GGUF`，修订 `8cca206c792af04f8d452368bd30b43ec18007f1`（ModelScope）。
- SHA-256：`7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5`，已与官方 Hugging Face 文件摘要交叉核对。
- 下载清单、README 和许可证在同一目录；清单为 `download-manifest.json`。

## 推理位置与配置

使用板上已有的 `/usr/share/cix/bin/llama-server`，版本 5929（837cc941），由 **P1 CPU** 执行推理。启动脚本使用 8 个推理线程、8192 token 上下文、单路推理，关闭思考模式并启用工具调用模板。

板上项目根 `.env` 已设置：

```dotenv
LABAGENT_CHAT_PROVIDER=api
LABAGENT_LLM_BASE_URL=http://127.0.0.1:8080/v1
LABAGENT_LLM_MODEL=qwen3-4b-local
LABAGENT_LLM_API_KEY=
LABAGENT_HTTP_TIMEOUT=180
```

`api` 表示通过 HTTP 接口调用模型；这里的地址指开发板本机，实际运行的模型就是上述 GGUF。API Key 留空。修改配置后需重启主服务。原配置已备份在板上 `.tmp/board-runtime/env-before-local-llm-20261027T111630Z.bak`，权限为 0600；该备份含原密钥，不应提交或对外分享。

语音配置继续为：

```dotenv
LABAGENT_ASR_BASE_URL=http://127.0.0.1:8010/v1
LABAGENT_ASR_MODEL=sensevoice-small-cix
LABAGENT_ASR_API_KEY=
```

配置切换仅修改了对话提供方式、LLM 地址/名称/密钥及请求超时；其他配置保持原值。模型失败会明确报错，不会自动改用外部大模型。

## 启动与访问

本次已启动服务；不要重复运行。开发板重启后，在项目目录分别打开三个终端：

```bash
# 终端一：本地对话模型
bash scripts/start_local_llm.sh
```

```bash
# 终端二：NPU 语音识别
.venv-asr/bin/python -m labagent.speech_service
```

```bash
# 终端三：LabAgent 网页与后端
.venv-linux/bin/python -m labagent
```

在板上浏览器访问 `http://127.0.0.1:8000`。如从电脑浏览器访问，先在电脑终端运行并保持连接：

```bash
ssh -N -L 127.0.0.1:18000:127.0.0.1:8000 radxa@192.168.12.210
```

然后打开 `http://127.0.0.1:18000`。浏览器采集其所在设备的麦克风与摄像头，模型计算仍在 P1。

三个健康检查分别为：

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8000/api/health
```

对话服务的模型列表可用 `curl http://127.0.0.1:8080/v1/models` 检查，别名应为 `qwen3-4b-local`。主服务的 `llm_configured=true` 只表示配置完整，应结合模型服务健康检查判断可用性。

## 已执行的验证与性能边界

`scripts/check_local_llm.py` 对实际本地模型执行了六项检查：中文非思考回答、状态工具协议、手册检索工具协议、真实 Agent 手册问答、暂停建议等待批准、物理确认请求不改写实验事实。记录为板上 `.tmp/board-runtime/llm-validation.json`。

协议检查中的固定状态用于验证工具结果往返；Agent 检查使用真实规则、项目自带 SOP、SQLite 和本地模型，数据库位于隔离临时目录。这不是物理实验或模型准确率认证。

首次中文短问句约 2.84 秒；携带实验上下文的三个 Agent 问题约 45.8–51.8 秒。短问句单次生成速度约 13.9 token/s。以上是本次样例测量，受提示词长度、缓存、输出长度、并发、温度与系统负载影响，不是稳定性能承诺。

也试跑了已有 Vulkan 后端：当前运行库下首次短请求约 33.0 秒，因此本次采用已完整验收的 CPU 后端，GPU 比较实例已停止。该结果不代表所有配置下 GPU 都较慢。

主服务 HTTP 接入和 ASR 复验记录为 `.tmp/board-runtime/llm-main-http-validation.json`，部署信息为 `llm-deployment.json`，日志位于同一目录。验收会话标记为回放且无真实测量；验证未调用外部 LLM API。当前没有设置开机自启。

## 重新验收

如更新模型或推理库，可在板上重新运行：

```bash
.venv-linux/bin/python -u scripts/check_local_llm.py
```

该检查需要模型已启动，约需数分钟，使用独立临时实验记录。不要把能正常聊天等同于所有工具和流程已验收。

来源：[CIX 模型条目](https://modelscope.cn/models/cix/ai_model_hub_26_Q2/resolve/master/models/Generative_AI/LLM/Qwen3-4B.md)、[作者官方 GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF)、[llama.cpp 工具调用说明](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)。
