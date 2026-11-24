# 第三方来源与边界

本目录是 CIX P1 / Radxa Orion O6 上 Qwen3-4B 的独立适配实验，不是正式 Agent 服务的替代品。

- **Based on tinygrad-npu-examples by Leonardo Marques (https://github.com/Reonarudo/tinygrad-npu-examples).**
  固定提交 `e264a1b78ca026494759cdddf2328f8e181324fb`；使用其 `ornith-9b/gguf_read.py` 读取/解量化 GGUF。该项目为 AGPL-3.0-or-later，另有保留署名、标记修改的附加条款。原 `LICENSE`、`NOTICE` 随 `vendor/tinygrad-npu-examples` 保留。这里的探针和适配代码是为当前板卡新编写的实验代码，不冒充上游原版；没有将这套依赖接入现有网络服务。
- `vendor/tinygrad` 来自 https://github.com/Reonarudo/tinygrad 的 `zhouyi` 分支，固定提交 `f85bc234df93c92d6a1dab0c7134669ee0db978b`，MIT。源码未修改；`bootstrap.py` 仅在隔离进程内适配旧库名/旧头文件。
- Qwen3 层计算语义参考 Qwen 团队与 Hugging Face 的 Apache-2.0 实现：https://github.com/huggingface/transformers/blob/main/src/transformers/models/qwen3/modeling_qwen3.py 。本实验读取开发板原有 Qwen3-4B GGUF，不下载或替换完整权重。
- 原生 API ABI 参考 Arm China 的 Apache-2.0 头文件：https://github.com/Arm-China/Compass_NPU_Driver/blob/main/Linux/driver/umd/include/standard_api.h 。运行时使用板上已有厂商库，不修改驱动。
- 激活平滑实验采用 SmoothQuant 的通道重参数化思路：https://github.com/mit-han-lab/smoothquant 。本目录自行实现小规模投影实验，没有执行完整模型量化或宣称完整模型质量通过。

下载源归档的提交和 SHA-256 记录在 `2026-11-24/source-provenance.json`。原模型及厂商二进制仍适用其各自许可；本实验不改变这些许可。
