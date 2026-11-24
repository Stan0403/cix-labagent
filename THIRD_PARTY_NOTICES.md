# 第三方与许可说明

原项目目录未包含统一 LICENSE，本交付未添加替权利人授权的开源许可证。自有代码的公开许可需要由权利人选择；默认私有 GitHub 仓库可用于当前交付。

- llama.cpp：上游 MIT，固定提交 `57819b8d4b39d893408e51520dff3d47d1ebb757`，构建时获取原源码及 LICENSE。CIX 视觉桥是项目新增代码，保持改动可识别。
- Qwen3/Qwen3-VL、CIX 模型工件：适用各自模型卡与随附许可证。公开上游来源不等于所有派生权重都可无条件转授。
- OCR 微调使用 CIX PP-OCR 目录中的 ResNet34-vd + BiLSTM + CTC 模型，不等同于任意 PP-OCRv4 轻量结构；原许可证副本保存在 `research/ocr_training/assets/MODEL-LICENSE.txt`。
- PaddleOCR 结构参考为 release/2.7，Apache-2.0。训练获取脚本和来源摘要保留在研究目录。
- `research/npu_llm/vendor/tinygrad-npu-examples/ornith-9b/gguf_read.py` 来自 Leonardo Marques，固定提交 `e264a1b78ca026494759cdddf2328f8e181324fb`；AGPL-3.0-or-later 及上游附加署名要求，随附 LICENSE/NOTICE。仅独立研究探针依赖它，未接入正式 Web 运行路径。完整来源见该目录 `NOTICE.md`。
- tinygrad zhouyi 研究路线需要时按 `source-provenance.json` 获取固定提交 `f85bc234df93c92d6a1dab0c7134669ee0db978b`，遵循上游 MIT；本包不复制整个历史 vendor 树。
- YOLO 训练/编译及其模型适用训练框架和模型的实际许可，原始训练工程不在当前桌面。器材训练准备文档属于早期方案，不代表完整训练工件已经在包中。
- FastAPI、SQLite、ONNX Runtime、PyTorch 等依赖保留其原许可证；厂商 libnoe/NOE_Engine 由目标板镜像提供，未在源码中重新分发。
