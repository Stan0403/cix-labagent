# YOLO / OCR 模型下载与板端来源

2026-11-25 已从正在运行的 O6 只读导出 YOLO 和液晶 OCR。10 个源文件合计 119,466,957 字节，下载前后板端摘要与本地逐文件 SHA-256 全部相同。未重启服务、替换权重或写入实验记录。

同一私有仓库的 [Release 模型附件](https://github.com/Stan0403/cix-labagent/releases/tag/v0.1.0-delivery)：

| 模型包 | 内容 | 当前执行位置 |
| --- | --- | --- |
| [yolo26n-o6-npu.zip](https://github.com/Stan0403/cix-labagent/releases/download/v0.1.0-delivery/yolo26n-o6-npu.zip) | 正式 packed `.cix`、AIPU 编译中间产物、类别顺序及板端验证 JSON | CIX NPU，960 输入 |
| [lcd-reader-v3.zip](https://github.com/Stan0403/cix-labagent/releases/download/v0.1.0-delivery/lcd-reader-v3.zip) | 正式 `rec-lcd.onnx`、字典及原模型许可证 | CPU ONNX Runtime |

本次从板端重新下载的 OCR 权重和字典，与已上传的上述 OCR 包内容一致；复用该附件，未生成另一份内容相同的大附件。新的板端来源、健康状态、文件大小与摘要见 [导出清单](../evidence/model_exports/2026-11-25/board-model-manifest.json)。

## 一键下载并校验

仓库为私有，需要 GitHub CLI 登录有权限的账号。克隆当前 `main` 分支后，在仓库根目录运行：

```bash
gh auth login
python deploy/fetch_models.py
python deploy/fetch_models.py --verify-only
```

只下载其中一种：

```bash
python deploy/fetch_models.py --models yolo
python deploy/fetch_models.py --models ocr
```

使用非默认 gh 路径可传 `--gh /path/to/gh`。下载 ZIP 的 SHA-256 和安装后的每个文件均按 [工件目录](../deploy/board_model_artifacts.json) 校验；不同摘要的已有权重会阻止覆盖，需先自行备份或使用新的 `--destination` 目录。已经手动下载两个 ZIP 时，可用 `--archive-dir /path/to/zip-directory` 离线安装。

安装后：

```text
models/equipment-yolo26-v1/equipment_yolo26n_960_heads_int8_bias32_packed.cix
models/equipment-yolo26-v1/classes.json
models/lcd-v3/rec-lcd.onnx
models/lcd-v3/ppocr_keys_v1.txt
```

## 精确版本

- YOLO `.cix`：3,909,392 字节，SHA-256 `a4dd692a0eb944d2379ca98f3539f3a8a770560ddf91940b570c22cb0a01760d`。
- OCR `.onnx`：111,573,230 字节，SHA-256 `ec0b3f0b6090c41720d1bcccc940103fffce74b6db5d1aa044229d40e8655d35`。
- OCR 字典：26,249 字节，SHA-256 `28b2362ad4ab2dc38769aa72feb535e3a9ddb3fd2a7585a05920e6393b1dc7f7`。
- 正式 YOLO 类别顺序来自本次 `8035/health`：`multimeter, battery, red_probe, black_probe`。新包的 `classes.json` 是该响应的直接序列化，不使用早期训练准备文档中的其他顺序。

`.aipu.bin` 是编译中间产物，生产运行使用 `*_packed.cix`。当前板端该目录未找到原始 FP32 ONNX 或训练 `best.pt`，本包不声称包含它们，也不包含原始照片与训练集。包内原 README 是板端历史文件，其中关于其他训练工件的描述不代表它们已随本包导出。

## 复现范围

本次只补齐 YOLO 与 OCR 工件。完整异构服务仍需要 LLM/VLM/ASR 权重、匹配的 CIX 运行时和驱动；`fetch_models.py --verify-only` 仅验证这两个模型，不代替 `deploy/verify_models.py` 的完整栈检查。

OCR 单文件超过 GitHub 普通 Git 提交的 100 MiB 限制，模型通过本仓库 Release 分发，下载脚本、来源和摘要提交到 Git 代码目录。参见 [GitHub 大文件说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)。

11 月 24 日白皮书和交付快照保留为历史版本；其中“桌面尚缺 YOLO”和“原始 YOLO 验证 JSON 未在桌面”已由本次导出补齐，当前状态以本文和导出清单为准。
