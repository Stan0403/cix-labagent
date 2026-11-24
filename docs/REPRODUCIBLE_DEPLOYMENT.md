# 复现环境与 O6 部署

## 普通电脑

Python 3.11/3.12 + Git。运行 `python quickstart.py`，访问 `http://127.0.0.1:8001`。基础包精确版本见根目录锁文件。默认规则/人工流程，不需要模型。`quickstart.py` 不复制虚拟环境、不改全局 Python、不覆盖已有 `.env`。

## O6 必备环境

参考板：Orion O6 32 GB，ARM64 Linux，内核 6.6.89-3-sky1。保留厂商配套 NPU KMD/UMD，必须存在 `/dev/aipu`。构建工具为 Git、CMake、C/C++ 工具链、curl/OpenSSL 开发依赖；例如 Debian 系可准备 `build-essential cmake git libcurl4-openssl-dev python3-venv`。安装系统包由目标机管理员执行，不由脚本升级驱动。

CPU 运行时固定 llama.cpp `57819b8d4b39d893408e51520dff3d47d1ebb757`。NPU VLM 在相同提交上加入 `maintenance/o6_npu_vision_bridge.inc`；固定 320×320、四路 100×2560 输出，语言生成留在 CPU。不能将该桥用于任意分辨率或其他 VLM。

## 1. 准备模型工件

`deploy/model_manifest.json` 列出目录结构和已核验摘要。源板上现有模型通常在旧项目 `LabAgent/models/`；OCR v3 原在 `LabAgent-demo/data/ocr-training/20261122-user81/`。先在单独导出目录整理以下子目录，不改原始文件：

```text
models/
  qwen3-4b/                  Qwen3-4B-Q4_K_M.gguf + 原许可证
  qwen3-vl-4b-gguf/          Qwen3VL-4B-Instruct-Q4_K_M.gguf + mmproj...F16.gguf
  qwen3-vl-4b-npu/           vision.cix + 原许可证
  equipment-yolo26-v1/       equipment_yolo26n_960_heads_int8_bias32_packed.cix
  sensevoice-cix/            整个ASR目录，包括权重、tokenizer、CMVN及model/config.yaml
  lcd-v3/                   rec-lcd.onnx + ppocr_keys_v1.txt + 许可证
```

```bash
python3 deploy/export_models.py --models /path/to/prepared/models --output cix-labagent-models.zip
```

脚本计算全部 SHA-256，写入压缩包内 `models/ARTIFACT_LOCK.json`。输出已存在则拒绝覆盖。将包传入新 O6，解压到源码根目录，运行：

```bash
python3 deploy/verify_models.py
```

当前桌面未保存 YOLO 和完整 ASR/VLM 工件，因此本交付不能凭空生成其下载地址或摘要。随附 `lcd-reader-v3.zip` 含可用 OCR 权重；完整模型包需在源板导出。模型包应作为有权限的 Release/私有存储附件，遵循原模型许可。

## 2. 创建独立 Python 环境

```bash
python3 quickstart.py --install-only
.venv/bin/python -m pip install -r requirements-dev.lock.txt
python3 -m venv .venv-npu
python3 -m venv .venv-asr
.venv-npu/bin/python -m pip install numpy==1.26.4
.venv-asr/bin/python -m pip install -e . -r legacy/LabAgent/requirements-asr.txt
```

ASR 独立使用 NumPy 1.26.4，避免与其他视觉环境的 NumPy 2 声明混装。厂商 wheel 路径来自目标板，必须选择与该镜像匹配的实际文件。例如原板使用：

```bash
for py in .venv/bin/python .venv-npu/bin/python .venv-asr/bin/python; do
  "$py" -m pip install --no-deps \
    /usr/share/cix/pypi/libnoe-2.0.0-py3-none-manylinux2014_aarch64.whl \
    /usr/share/cix/pypi/NOE_Engine-2.0.0-py3-none-manylinux2014_aarch64.whl
done
```

若板端名字不同，按实际 SDK 修改；不要从不明位置下载二进制代替。分别运行各环境 `python -m pip check` 并检查 `import NOE_Engine`。厂商 wheel 不在通用锁文件中。研究训练依赖单独见 `research/ocr_training/requirements-torch.txt`，不需要装进生产服务环境。

## 3. 构建固定推理运行时

```bash
python3 deploy/build_runtime.py --kind both --jobs 4
```

生成 `.deps/llama-cpu/build/bin/llama-server` 和 `.deps/llama-npu/build/bin/llama-server`。CPU 启用 KleidiAI、ARMv9/i8mm/dotprod/sve；这条复现构建关闭 Vulkan，保留本板实测更快的 CPU 路线。历史 GPU 测试的原始构建脚本在 `maintenance/o6_build_vulkan.py`，其中包含旧板路径。

## 4. 一键启动服务栈

确认原项目不占用端口后执行：

```bash
.venv/bin/python deploy/o6_stack.py --apply
.venv/bin/python deploy/check_stack.py --wait 180
```

默认使用当前仓库内模型和运行时。复用已有隔离环境时，可用 `--models`、`--cpu-server`、`--npu-server`、`--npu-python`、`--asr-python` 指定绝对路径。先用 `--render-only` 审阅 `deploy/generated/`；这会跳过硬件验收。

用户服务采用 `cix-labagent-*` 新名称；已有同名单元时拒绝覆盖。模型启动慢时查看 `journalctl --user -u cix-labagent-llm -n 80`。停止所有服务：`systemctl --user stop cix-labagent.target`；取消登录时自启：`systemctl --user disable cix-labagent.target`。需要退出登录后继续运行时由管理员设置 linger；本脚本不自动修改系统设置。

没有执行整机重启验收。准备跨机器部署前记录实际 OS、Python、CMake/编译器、KMD/UMD、厂商 wheel 和完整工件锁。不能以健康接口 200 证明已完成真实推理；验收还应调用存量图片/音频，并检查 NPU 请求计数及后端字段。

## 5. 使用与数据备份

网页默认仅回环。远程电脑建立 `ssh -N -L 18001:127.0.0.1:8001 user@board` 后打开 `http://127.0.0.1:18001`。浏览器使用访问设备的相机/麦克风。SQLite 备份优先使用 Python `sqlite3.Connection.backup`，不要在写入时直接复制单个数据库文件。

新部署不能从旧 README 的固定路径、旧端口或过时后端名称推断当前状态；以当前根 README、工件锁、服务环境和真实 `/health`/推理响应为准。
