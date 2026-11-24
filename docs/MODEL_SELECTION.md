# CIX 26_Q2 模型选型建议

日期：2026-10-27。状态：Qwen3-4B 本地对话与 SenseVoice NPU 语音已在 P1 部署，其余条目仍是后续方案。实际运行配置见 [LOCAL_LLM.md](LOCAL_LLM.md)。

## 来源约束

用户要求项目相关的所有模型均从 `cix/ai_model_hub_26_Q2` 选取。后续选型、辅助模型和替代模型均应符合这个范围。

唯一选型目录：[CIX 26_Q2 models](https://modelscope.cn/models/cix/ai_model_hub_26_Q2/tree/master/models)。本次通过仓库 API 读取并核对了实际目录及下列说明文件。

“选型在库中”与“权重直接存于该仓库”需要分别记录：当前 `models/Generative_AI/LLM/` 中对应候选是 Markdown 部署说明；说明指向模型作者的官方权重来源，然后使用 llama.cpp 或 MNN 转换、量化并运行。不能宣称该目录直接附带了这些 GGUF 权重。按库内说明指定的官方来源获取，仍须记录具体模型、量化方式、修订号和文件校验值。

## 对话模型首选

实际部署 **Qwen3-4B，作者官方 GGUF Q4_K_M，关闭思考模式，由 P1 CPU 执行**。复用了作者现成量化文件，无需自行重新量化。CPU 已通过六项真实模型检查；当前 Vulkan 首次请求较慢，比较实例已停止。

- 库内条目：`models/Generative_AI/LLM/Qwen3-4B.md`。
- 库内已提供 llama.cpp CPU、Vulkan GPU 和 MNN 的部署示例；文档中的 Q4_0 权重大小约 2.20 GiB，运行内存还需加上上下文缓存和工作区。
- Qwen 官方模型支持思考/非思考切换、指令跟随和工具使用；需在本项目中验证实际效果，不能把通用能力说明当作已通过 Agent 验收。
- 推理服务已采用 llama-server 的本机 Chat Completions 接口，地址为 `http://127.0.0.1:8080/v1`，模型别名 `qwen3-4b-local`，已启用工具调用模板。现有 Gateway 继续使用 HTTP 适配；`CHAT_PROVIDER=api` 表示接口协议，是否端侧取决于地址及实际服务。
- 候选备选：库内 `Qwen2.5-3B-Instruct` Q4_0，文档权重约 1.69 GiB。若响应速度或当前运行库兼容性更重要，可与首选用同一套问题测试后决定。

库中的测速属于其测试环境，Q4_0 的结果也不能直接套用到本次 Q4_K_M。Qwen3 本次短问句约 2.84 秒，携带实验上下文的三个问题约 45.8–51.8 秒；Qwen2.5 备选未在该板上测试。

## 全项目模型清单

| 用途 | 库内选择与目录 | 目标计算位置 | 当前状态与条件 |
|---|---|---|---|
| 对话与 Agent | `Generative_AI/LLM/Qwen3-4B.md` | P1 CPU，GGUF Q4_K_M | 已部署，已验证真实工具调用及批准流程 |
| 语音识别 | `Audio/Speech_Recognition/onnx_sensevoice` | NPU 推理，CPU 音频前后处理 | 已部署并实测 |
| RAG 语义编码 | `Generative_AI/Text_Image_Search/onnx_bge_small_zh`，BGE-small-zh-v1.5 | NPU 编码，CPU 检索 | 库内有 `bge-small-zh_256.cix`；尚未接入，需按 256 token 输入限制分块 |
| 显示屏 OCR | `ComputeVision/OCR/onnx_PP_OCRv4` | NPU 推理，CPU ROI 与后处理 | 库内有检测、方向分类、识别三个 `.cix`；需验证小数点、单位和液晶/数码管读数 |
| 器材与场景理解 | `MultiModal/Qwen2.5-VL-3B-Instruct` | 按库内方案：NPU 视觉编码 + CPU 文本模块 | 库内有视觉编码 `.cix` 和 NPU/MNN 示例，文本权重按其文档获取；尚未适配本项目 |
| 后续实时器材检测 | `ComputeVision/Object_Detection/onnx_yolov8_n`，YOLOv8n | 微调和编译后使用 NPU | 库内列出模型、构建配置和示例，但本次目录未列出该预编译权重；默认 COCO 类别不覆盖本实验四类器材，不能直接宣称可用 |

第一版器材理解可先评估上述 VLM；实时检测的 YOLOv8n 另行准备实验器材数据集，使用库内模型为基础微调、编译、评测。新增依赖模型也需核对库内来源，不能默默引入库外 CLIP、重排器或其他模型。

## 切换对话模型的验收

1. 在独立本机端口启动模型服务，确认实际权重、推理后端、内存、首字延迟与回答延迟。
2. 用项目真实上下文验证中文问答、引用、无依据问题和长输入；关闭思考模式，控制输出长度。
3. 验证 `get_status`、`search_manual`、`request_action` 的结构化调用、参数校验、工具结果读取与用户批准流程。能聊天不等于完成 Agent 接入。
4. 验证模型不能代替现场人员确认电源、接线或编造测量，不因普通进度问答改变实验状态。
5. 验收通过后备份 `.env`，将 LLM 地址切换到本机服务；ASR 配置保持现有本机服务。失败明确报错，不自动回连外部模型。

## 主要依据

- [库内 Qwen3-4B 部署说明](https://modelscope.cn/models/cix/ai_model_hub_26_Q2/resolve/master/models/Generative_AI/LLM/Qwen3-4B.md)
- [库内 Qwen2.5-3B 部署说明](https://modelscope.cn/models/cix/ai_model_hub_26_Q2/resolve/master/models/Generative_AI/LLM/Qwen2.5-3B-Instruct.md)
- [Qwen3-4B 作者模型说明](https://huggingface.co/Qwen/Qwen3-4B)
- [llama.cpp 工具调用文档](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)

库内 README 的版本、参数与测速描述也应结合真实文件核对，不能将其所有文字直接当作本机验收结果。
