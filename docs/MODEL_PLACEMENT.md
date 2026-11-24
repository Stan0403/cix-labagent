# 初赛设计与当前模型部署

依据：用户提交的《群聊(3)-初赛项目信息(1).pptx》第 5 页，以及项目计划书中的 CIX P1 异构计算方案。下表的“原规划”是设计目标，不是当前已完成的能力或实测指标。

| 模块 | 初赛规划 | 当前实现 |
|---|---|---|
| Agent、规则、状态、工具、报告 | P1 CPU | FastAPI/Python/SQLite 在 P1 CPU 执行 |
| 对话 LLM | Qwen2.5-3B，INT4，P1 CPU+GPU；其他版本材料也列过 MiniCPM3-4B 备选 | 已按 CIX 26_Q2 库选型，部署本地 Qwen3-4B Q4_K_M，P1 CPU 推理；详见 LOCAL_LLM.md |
| 语音 ASR | Whisper Tiny，INT8，P1 NPU | 已用 CIX 26_Q2 SenseVoiceSmall `.cix` 在 P1 NPU 实测，CPU 负责音频前后处理；详情见 LOCAL_ASR.md |
| 器材检测 | YOLOv8n，INT8，P1 NPU | 已有 YOLOv8 ONNX 接口；当前板上未配置实验器材模型，也未接 NPU 检测后端 |
| 显示屏 OCR | PP-OCRv4，INT8，P1 NPU，按 ROI 触发 | 已有 ROI OCR/VLM 接口；当前板上未启用，未集成 PP-OCRv4 |
| RAG 向量编码 | BGE-small-zh-v1.5，INT8，P1 NPU 编码、CPU 向量检索 | P1 CPU 上字符 n-gram TF-IDF 检索本地知识；尚无 BGE 语义向量模型 |
| 可选视觉大模型 | Qwen2.5-VL-3B，INT4，P1 GPU+CPU；视觉编码器计划适配 NPU | 已有 VLM API 接口，当前未配置、未部署本地 VLM |

训练/微调原计划在开发电脑完成，P1 运行量化推理、知识索引与实验记录。浏览器位于哪台设备，就使用哪台设备的摄像头和麦克风；网页所在设备不等于模型所在设备。

当前对话模型和语音模型均在 P1 运行；视觉、OCR 与 BGE 尚未部署，因此完整多模态方案仍需逐项验收。模型选型均限定于用户指定的 CIX 26_Q2 库，清单见 MODEL_SELECTION.md。
