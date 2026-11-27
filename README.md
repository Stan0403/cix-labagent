# LabAgent：O6 本地实验助手

当前应用代码取自 **2026-11-27 的 O6 板端运行目录**，已同步队友对器材检查、仪表检查、按键读数和 Agent 调度的更新。应用实现保持板端原样；发布时仅修正了一个未随30秒推理超时更新的测试断言。源码清单与差异见 [SOURCE_SNAPSHOT.json](SOURCE_SNAPSHOT.json) 和 [同步记录](evidence/board_refresh/2026-11-27/sync-summary.json)。

[公开仓库](https://github.com/Stan0403/cix-labagent) · [新版技术白皮书](docs/CIX-LabAgent-Technical-Whitepaper.pdf) · [本次源码和白皮书发布包](https://github.com/Stan0403/cix-labagent/releases/tag/board-2026-11-27) · [YOLO / OCR 模型下载](docs/MODEL_ARTIFACTS.md)

## 一键运行

普通电脑需要 Python 3.11/3.12、Git 和可用的 Python 包源。首次运行会创建独立 `.venv` 并安装锁定依赖。

```bash
git clone https://github.com/Stan0403/cix-labagent.git
cd cix-labagent
python quickstart.py
```

Linux/macOS 使用 `python3 quickstart.py`。打开 **http://127.0.0.1:8001**，Ctrl+C 停止；端口冲突可加 `--port 18001`。已有 `.env` 不会被覆盖。

新电脑默认使用规则和人工输入，可以创建实验、记录读数和导出报告。自动识别、语言规划及语音需要接入真实模型服务，未配置时不生成模拟结果。模型不随普通 Git 提交分发。

## 当前交互

1. 导入要求或实验手册，核对计划后开始实验。
2. 器材预览使用 YOLO。缺少器材时等待画面变化；齐全后进行两次正式独立观察，第二帧和通过后的推进由规则完成。
3. 仪表设置从同一张照片分别放大旋钮和插孔，使用 NPU 视觉编码和 CPU 短文本判断。首次自动检查，未通过后按页面提示重试。
4. 测量阶段点击“已接好，开始读数”，经过5秒准备倒计时后定位 LCD 并识别单帧。快速采集不调用慢 VLM 回退；数值按计划范围、量程和时效条件记录，保留实际正负号。零值、负值可记录并提示核对。
5. 报告将该入口的数据标为“单帧显示屏识别”。旧双帧接口仍保留稳定性核验；单帧记录不等于双帧稳定性证明。

SQLite 保存记录和审计。暂停、切换、刷新或删除后，旧会话版本的结果不能继续提交；删除需要明确确认。取景相机属于打开浏览器的设备，模型在 O6 上运行。

## O6 部署与模型分工

参考板为 Radxa Orion O6 / CIX P1，32 GB，ARM64 Linux，内核6.6.89-3-sky1。当前进程、配置、模型摘要和依赖版本已保存在 [runtime.json](evidence/board_refresh/2026-11-27/runtime.json)。

| 模块 | 当前执行位置 | 接口 |
| --- | --- | --- |
| Web、流程、TF-IDF、SQLite与报告 | CPU | 8001 |
| Qwen3-4B Q4_K_M 规划/对话/工具 | CPU，8个大核线程、KleidiAI | 8081 |
| YOLO26n INT8 四类器材 | CIX NPU，CPU前后处理 | 8035 |
| 微调PP-OCR液晶识别器 v3 | CPU ONNX Runtime，4线程 | 经8031 |
| Qwen3-VL通用视觉 | CPU | 8032 |
| 仪表旋钮与插孔核验 | NPU视觉编码 + CPU文本 | 8034 + Unix socket |
| SenseVoiceSmall | NPU网络 + CPU音频前后处理 | 8011 |

应用环境为 Python3.11.2、NumPy2.4.6、ONNX Runtime1.31.1；ASR/NPU独立环境使用 NumPy1.26.4。便携电脑依赖锁见 `requirements.lock.txt`，测试环境见 `requirements-dev.lock.txt`。不能将 Windows 虚拟环境复制到 ARM Linux。

YOLO 和 OCR 与既有发布权重的摘要一致。使用 GitHub CLI 下载并校验：

```bash
python deploy/fetch_models.py
python deploy/fetch_models.py --verify-only
```

公开附件可在浏览器直接下载；无 CLI 的离线安装方式见 [模型说明](docs/MODEL_ARTIFACTS.md)。完整异构服务还需准备 LLM/VLM/ASR 工件、配套厂商 wheel 和 `/dev/aipu`，详见 [部署文档](docs/REPRODUCIBLE_DEPLOYMENT.md)。准备完成后：

```bash
python deploy/build_runtime.py --kind both
.venv/bin/python deploy/o6_stack.py --apply
.venv/bin/python deploy/check_stack.py --wait 180
```

安装器先检查工件和端口；新服务使用 `cix-labagent-*` 名称，已有服务占用端口时停止。本文不代表已在另一块全新 O6 上完成安装或重启验收。

## 实测与验证

11月27日对常驻模型服务进行串行 HTTP 采样：

| 请求 | 次数 | HTTP平均耗时 |
| --- | ---: | ---: |
| LLM简短中文 / 带说明问答 / 工具调用 | 各2次 | 1.694 / 3.919 / 4.337秒 |
| YOLO完整场景图 | 预热1次、计时5次 | 0.089秒 |
| OCR `.419` / `-0.00` / `1.353` 裁剪图 | 各3次 | 0.283 / 0.234 / 0.233秒 |
| 旋钮与插孔完整核验 | 2次 | 11.003秒 |

OCR `.419` 样例因小数点位置冲突被拒收。数据不包含取景、倒计时或数据库写入；同图重复和开发诊断图不是独立准确率评测。原始逐次响应、输入摘要及完整条件见 [model-benchmark.json](evidence/board_refresh/2026-11-27/model-benchmark.json)。

在已有 O6 模型服务上复现采样：

```bash
python scripts/benchmark_deployed_models.py --scene /path/to/equipment-photo.jpg --output data/benchmarks/o6.json
```

该脚本只请求模型接口，不调用实验会话 API；原始照片不嵌入输出。不同照片、输出长度、缓存及并发条件会改变结果。

软件验证：本地350项通过；O6临时源码副本346项通过。原始板端7个失败来自旧的12秒超时测试断言，发布仓库按现行30秒实现修正后通过，原报告仍保留。模型 fixture 的通过数不表示模型准确率。运行测试：

```bash
python -m pip install -r requirements-dev.lock.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m pytest --junitxml=test-results.xml
```

GitHub CI 入口：[软件回归](https://github.com/Stan0403/cix-labagent/actions/workflows/ci.yml)。

## 仓库内容

`labagent/` 为当前应用；`configs/`、`knowledge/`、`fixtures/` 和 `tests/` 为计划、手册及验证材料；`scripts/` 提供模型服务和采样工具；`deploy/` 提供下载、校验及部署入口；`evidence/` 保留原始测量与回执。

`legacy/`、`research/`、`maintenance/` 和较早专项文档保留历史版本。当前行为以代码、本文及 [CURRENT_STATE.md](docs/CURRENT_STATE.md) 为准；板端原README另存为 [BOARD_README.md](docs/BOARD_README.md)。

默认仅本机访问。远程电脑可用 `ssh -N -L 18001:127.0.0.1:8001 user@board` 建立隧道，再访问 `http://127.0.0.1:18001`。相机图像不能证明断电、表笔电气接触或无短接。

原项目未提供统一开源许可证；第三方与模型许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。正式数据库、原始拍摄批次、密钥和虚拟环境不提交 Git。
