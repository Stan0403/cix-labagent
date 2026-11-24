# P1 本地语音识别

项目增加独立的 SenseVoiceSmall 语音服务，网页录音经主服务转发到板上的 ASR 服务。语音只转成文本，核对后再发送给 Agent。对话也已切换为板上 Qwen3-4B，三个服务的启动说明见 [LOCAL_LLM.md](LOCAL_LLM.md)。

## 模型与运行位置

- 来源：[CIX 26_Q2 模型库](https://modelscope.cn/models/cix/ai_model_hub_26_Q2/tree/master/models/Audio/Speech_Recognition/onnx_sensevoice)。
- 权重：`models/sensevoice-cix/sense_voice_mask256.cix`，495,032,896 字节；下载清单和 SHA-256 在该目录的 `download-manifest.json`。
- CIX NPU 执行神经网络；P1 CPU 使用 PyAV 解码音频、Kaldi fbank/LFR/CMVN 前处理及 SentencePiece/CTC 后处理。
- 独立环境 `.venv-asr` 使用 NumPy 1.26.4，避免与视觉开发环境的 NumPy 2 依赖冲突。
- 复用板上 `/usr/share/cix/pypi/` 的 `libnoe`、`NOE_Engine` wheel；不升级系统驱动。

## 2026-10-27 板端验收

- NPU 实际初始化、加载图、创建任务、推理和释放成功；没有使用 CPU 或云端替代模型推理。
- 原始中文 MP3（5.616 秒）转写为“开放时间早上9点至下午5点。”，一次处理约 0.325 秒；英文示例也返回了实际转写。该时间包含模型前后处理和推理，不含加载、音频上传及解码，不代表准确率或稳定性能指标。
- 经 WebM/Opus 转码后通过主服务 `/api/audio/transcribe` 完成真实转写，但“开放”被识别为“开饭”。16.848 秒 WAV 的两段处理通过，切分后也出现错字；这些验证证明链路可运行，尚未建立自有口令准确率基线。
- 无效音频返回 422，两个服务在 SSH 断开并重连后均健康；ASR 环境 `pip check` 通过。
- 语音接入时配置了本地 ASR 地址并保留当时的 LLM 配置；之后对话已切为本地 Qwen3-4B。此处模型样例测试未调用外部对话 API；用户麦克风的排查和采集测试是另行执行的。
- 板端记录：`.tmp/board-runtime/asr-deployment-20261027T082535Z.json`、`asr-http-verification.json`；日志同目录。主服务 PID 5947、ASR PID 5916 是当时状态，重启后会改变。

## 配置

在板上项目根目录 `.env` 中设置以下三项；保留原来的大模型配置：

```dotenv
LABAGENT_ASR_BASE_URL=http://127.0.0.1:8010/v1
LABAGENT_ASR_MODEL=sensevoice-small-cix
LABAGENT_ASR_API_KEY=
```

修改后重启主服务。这个地址指开发板本机；不需要云端语音 API Key。对话模型是否在本地由独立的 `LABAGENT_LLM_*` 配置决定。

## 手动启动（板上两个终端）

先进入实际项目目录 `/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent`。

终端一：

```bash
.venv-asr/bin/python -m labagent.speech_service
```

终端二：

```bash
.venv-linux/bin/python -m labagent
```

已经运行时不要重复启动。ASR 服务仅监听 `127.0.0.1:8010`，主服务默认 `127.0.0.1:8000`。可分别检查：

```bash
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8000/api/health
```

ASR 健康检查必须返回 HTTP 200 和 `status=ok`；初始化失败返回 503。主服务的 `asr_configured=true` 仅表示已填配置，不等于模型健康检查通过。

如从电脑浏览器访问，在电脑终端建立隧道并保持终端打开：

```bash
ssh -N -L 127.0.0.1:18000:127.0.0.1:8000 radxa@192.168.12.210
```

然后访问 `http://127.0.0.1:18000`，刷新页面，创建或选择实验，点击录音，允许麦克风权限，停止后核对转写再发送。这里使用电脑的麦克风，ASR 计算在 P1；要使用板上的麦克风，应在板上的浏览器操作。

## 范围与排查

- 浏览器录音最长 60 秒、文件最大 16 MB；支持可解码的 WebM/Opus、WAV、MP3 等音频。
- 模型固定输入为 252×560 声学特征，当前按 12 秒分段；切分处和噪声会影响识别，尚未做自有实验口令准确率评测。
- 默认普通话；服务接口另支持 `auto/en/yue/ja/ko` 的 `language` 表单字段。
- 繁忙时拒绝第二个任务；初始化或推理失败时返回错误，不会自动改用云端或模拟转写。
- `/health` 不可用先检查 `.tmp/board-runtime/` 下本次 ASR 日志、模型文件和 `/dev/aipu` 权限；只有主服务不可用则检查主服务日志。
- 本次启动不代表开机自启；重启开发板后按以上两个终端命令启动。

## 环境复建

基础 Python 要求 3.11。已有环境无需重复安装；新建独立环境后，在该环境内执行：

```bash
.venv-asr/bin/python -m pip install -e . -r requirements-asr.txt
.venv-asr/bin/python -m pip install --no-deps \
  /usr/share/cix/pypi/libnoe-2.0.0-py3-none-manylinux2014_aarch64.whl \
  /usr/share/cix/pypi/NOE_Engine-2.0.0-py3-none-manylinux2014_aarch64.whl
```

wheel 名称应以目标板实际 SDK 为准；不能把 Windows 虚拟环境直接复制到 ARM Linux 使用。
