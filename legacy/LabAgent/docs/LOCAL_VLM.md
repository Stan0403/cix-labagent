# Qwen2.5-VL-3B 本地视觉模型

选用 CIX 26_Q2 模型库中的 Qwen2.5-VL-3B-Instruct，使用其说明文档引用的 MNN 官方预转换权重（ModelScope `MNN/Qwen2.5-VL-3B-Instruct-MNN`，固定版本 `57e28fbba82bafcd552b3171c59a3d93c4deccf2`）。这是 CPU 推理方案，视觉编码和文本生成都不使用 NPU。模型下载到 `models/qwen25vl-mnn/`，独立环境为 `.venv-vlm`。

板端运行 `bash scripts/start_local_vlm.sh`，以 `curl http://127.0.0.1:8030/health` 检查是否加载完成。LabAgent 配置为：

```text
LABAGENT_OCR_PROVIDER=mnn
LABAGENT_OCR_BASE_URL=http://127.0.0.1:8030
```

服务仅监听本机，更新配置后需重启主服务。网页中框选屏幕，并逐次人工确认旋钮上的直流电压档位。视觉模型返回的数字只作为预览；置信度不作为安全依据，不自动写入测量。请人工核对小数点和符号后输入实际值。

开发板 CPU 实测：模型文件已通过 SHA-256 校验；一张显示 1.34 V 的实拍图经独立服务和临时会话测试，模型描述包含 1.34，独立服务的一次请求约 8 秒。此前使用另一提示词时，同一张图被误读为 -1.34，因此这不是准确率结论，正负号、小数点和其他数字都需要更多实拍样本验证。恢复原 OCR 可将 `LABAGENT_OCR_PROVIDER` 改回 `cix`、地址改回 `http://127.0.0.1:8020`，并重启主服务。
