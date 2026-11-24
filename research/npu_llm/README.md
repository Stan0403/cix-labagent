# Qwen3-4B / Orion O6 NPU 适配实验（2026-11-24）

**当前没有完成完整 Qwen3-4B 的 NPU 迁移，也没有切换正式 Agent 的语言后端。** 已实现真实权重的线性层转换、原生 NPU 调用，以及首个 Transformer 层的 CPU/NPU 混合数值验证。这是后续开发的实验基线。

## 已实际实现

1. 恢复有线 SSH：电脑 `192.168.123.99`，O6 `192.168.123.2`，板端使用 `enp49s0`。临时 NetworkManager 配置 `codex-o6-direct-20261124` 不自动连接、不抢默认路由；重启后地址不保证保留。无线连接仍在使用。
2. 在 `/home/radxa/.local/share/codex-qwen3-npu` 建立隔离 Python 3.11 环境，安装板上厂商 ONNX Runtime / Zhouyi 算子轮子。不向正式 `.venv-app` 安装这些依赖，不升级内核或 NPU 驱动。
3. 从原 GGUF 读取 `blk.0.attn_q.weight`（4096×2560）。将线性投影改写成等价 FP16 1×1 Conv，导出 ONNX，再由板上 Zhouyi EP 编译为可执行 `aipu.bin`。真实 profiling 记录为 `ZhouyiExecutionProvider`；没有把普通 CPU 回退当作 NPU 成功。
4. `aipu_native.py` 通过板上 `libaipu_driver.so` 直接运行编译图，支持 FP16/FP32 原始缓冲区和有限任务超时。`ReloadingLinear` 每次重建上下文，绕过本机 UMD 的重复运行超时。FP16 投影连续 5 次改变输入均通过，误差约 0.021%。
5. `probe_first_block.py` 用原模型分词接口和真实实验提示词，执行完整第 0 层：RMSNorm、Q/K/V、QK Norm、RoPE、GQA、因果注意力、残差、SwiGLU；仅 Q 投影放到 NPU。分别验证 24-token 预填充和带该次 KV 缓存的 1-token 教师强制计算。其余 35 层和输出头没有执行，没有生成文本。
6. 实现 W8A8 投影：逐输出通道量化权重，NPU 执行标量尺度 QLinearMatMul，CPU 恢复通道尺度。另尝试输入通道激活平滑（alpha=0.5）。校准仅使用三条实验提示词、84 个第一层激活；另有一条提示词用于投影检查。这不构成完整模型量化或独立任务质量评估。

## 实测结论和边界

CPU 对照是 **同一份 GGUF 解量化后的 NumPy FP32 运算、OpenBLAS 4 线程**，不是正式 llama.cpp 的时延。以下均为层/投影实验，不是整个 Agent 的耗时或 tokens/s。

| 实验 | 结果 |
|---|---|
| FP16 混合首层、3 条提示词 | 通过；预填充层输出相对 L2 误差约 0.0052%–0.0060%，单 token 约 0.0030%–0.0033% |
| W8A8 + CPU 通道尺度恢复，3 条提示词中位数 | 能在 NPU 运行；预填充 Q 投影 CPU 14.1 ms → 混合 4.5 ms；单 token CPU 2.2 ms → 混合 3.8 ms。完整第 0 层预填充 CPU 254.3 ms → 混合 245.7 ms，单 token CPU 146.3 ms → 混合 148.4 ms |
| 上述 W8A8 的首层数值检查 | 未通过当前 0.5% 相对 L2 实验门槛；3 个样例预填充误差约 0.71%–0.81%。该门槛由本实验设定，不是语言模型质量验收标准 |
| 平滑量化 alpha=0.5，3 条提示词 | 未改善；预填充首层误差约 0.85%–1.00%，单 token 约 0.25%–0.39% |
| 直接 FP16 MatMul | 当前 EP 回退 CPU，判定失败 |
| 逐通道 QDQ MatMul | 当前运行时编译失败 |
| 直接逐通道 QLinearConv | NPU 输出与同一量化图 CPU 输出差异过大，不能使用；CPU 量化误差约 2.63%，NPU 对原浮点投影误差约 71.1% |
| tinygrad 直接提交 NPU kernel | 本机旧 KMD/编译器组合下简单 smoke 超时；没有改驱动来强行接入 |

FP16 的 ORT 冷调用、编译调试转储、原生调用和整个层的测量有不同开销；不要混用这些数字。原始逐项 JSON、profiling、日志和编译二进制在 `2026-11-24/results/`，汇总为 `adaptation-summary.json`。最初的 `board-preflight.json` 使用了不适用于本机的头文件/库名路径，存在假阴性，应以结果目录中的 `codex-qwen3-npu-preflight-final.json` 为准。

## 文件和复现

| 文件 | 用途 |
|---|---|
| `probe_real_linear.py` | 从真实 GGUF 导出多种投影图、编译、检查真实 provider 和数值；`--cache` 保留可执行 ELF |
| `aipu_native.py` | 编译图原生调用和重建上下文的线性层原型 |
| `probe_aipu_native.py` | FP16 原生投影连续运行检查 |
| `probe_first_block.py` | 首层预填充/单 token、CPU 与混合路径比较；`--quantized` / `--smooth` |
| `prepare_projection_inputs.py` | 小规模真实第一层激活的校准与投影检查输入 |
| `smoke_ort.py` | 可重复执行的基础 INT8 NPU smoke，带 provider 断言 |
| `bootstrap.py`、`smoke_npu.py` | 保留的 tinygrad 旧 ABI 兼容和失败诊断路线 |
| `board_preflight.py`、`check_existing_services.py` | 环境清单、正式服务健康及存量图片的 YOLO 实际推理复查 |
| `summarize_results.py`、`collect_results.py` | 汇总和归档当前隔离实验 |

以下在开发板隔离目录运行，使用本次已编译的二进制：

```bash
cd /home/radxa/.local/share/codex-qwen3-npu
timeout -k 5s 60s env OPENBLAS_NUM_THREADS=4 .venv/bin/python probe_first_block.py
timeout -k 5s 60s env OPENBLAS_NUM_THREADS=4 .venv/bin/python probe_first_block.py --quantized
timeout -k 5s 60s env OPENBLAS_NUM_THREADS=4 .venv/bin/python probe_first_block.py --quantized --smooth
```

重新编译普通 FP16 24-token 投影：

```bash
timeout -k 5s 45s env LD_LIBRARY_PATH=/usr/share/cix/lib/onnxruntime \
  TILING_METHOD=none OPENBLAS_NUM_THREADS=4 \
  .venv/bin/python probe_real_linear.py --mode conv --repeat 1 --cache
```

重新生成 W8A8 图，分别把 `--rows` 设为 24 / 1，把输入文件设为 `projection-validation24.npy` / `projection-validation1.npy`：

```bash
env OPENBLAS_NUM_THREADS=4 .venv/bin/python prepare_projection_inputs.py
timeout -k 5s 45s env LD_LIBRARY_PATH=/usr/share/cix/lib/onnxruntime \
  TILING_METHOD=none OPENBLAS_NUM_THREADS=4 \
  .venv/bin/python probe_real_linear.py --mode qhost --rows 24 --repeat 1 --cache \
  --input-npy projection-validation24.npy --calibration-npy projection-calibration.npy
```

激活平滑版本在最后增加 `--smooth-alpha 0.5`。编译导出的 `context-*.onnx` 每次采用新名称，避免厂商缓存文件拒绝覆盖。**当前浮点/混合图不要依赖同一会话的重复执行**，默认 tiling 也可能超时，因此命令保留外部超时，并指定 `TILING_METHOD=none`。失败探针会返回非零退出码并留下报告。

模型实际位置：`/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent/models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf`，SHA-256 `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5`。它已经是 GGUF 量化模型，本实验的 W8A8 是反量化后的再次量化；不能当作原始浮点模型的 W8A8 质量。

## 完整迁移还缺什么

需要在 LLM 运行时接通全部 36 层与输出头、支持不同长度的预填充和长 KV 缓存，并完成真实文本生成、工具 JSON、计划与对话的整模型质量和端到端耗时评估。现有固定形状首层原型不能直接替换 `llama-server`。

从当前实测推断，后续应优先研究**预填充的大矩阵运算**，逐 token 暂留 CPU；不能由一个投影的局部收益推断整个 Agent 会提速。量化需要原始浮点权重、更充分的 Agent 激活校准，并处理输出量化误差和上下文复用问题。上游直接 kernel 路线还涉及本机驱动兼容，不属于已验证能力。

## 正式系统状态

正式 Qwen3-4B 仍在 CPU `8081`，网页 `8001`、读表适配器 `8031`、ASR `8011` 健康检查通过；YOLO `8035` 使用已有照片完成实际 NPU 推理复查。没有调用实验会话的写入接口或修改正式读表模型。SSH 会话保留。

来源、固定提交与署名见 [NOTICE.md](NOTICE.md) 和 `2026-11-24/source-provenance.json`。主要 ABI/算子限制依据 [Arm China Driver API](https://github.com/Arm-China/Compass_NPU_Driver/blob/main/Linux/driver/umd/include/standard_api.h)、[Zhouyi EP 算子支持表](https://github.com/Arm-China/Compass_Onnxruntime/blob/rel-1.22.0/zhouyi/docs/Support_Ops_Model_X2.md)；本机是否成功以真机报告为准。
