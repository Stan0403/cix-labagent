# Qwen3-VL-4B 本地视觉服务

2026-11-17：已验证真实 Mali GPU，混合 GPU 路径无整体加速、全 GPU 场景超时，未用于正式服务。读表与步骤检查继续使用下述 CPU 配置；器材检查已另行启用 NPU 视觉编码 + CPU 语言。结果见 [MODEL_ACCELERATION.md](MODEL_ACCELERATION.md)。

官方 NPU 视觉编码器已按用户要求接入正式 `8031/inspect`，后台模型为 `8034`。完整业务实测约 29.9 秒；详情见 [NPU_EQUIPMENT.md](NPU_EQUIPMENT.md)，包含数值兼容性限制、验证与回退。

模型选自 CIX `ai_model_hub_26_Q2` 的 `models/MultiModal/Qwen3-VL-4B-Instruct.md`。
该目录给出转换和 CPU/Vulkan 推理指南；本部署使用同一模型的 Qwen 官方 GGUF 权重，避免在开发板上重新转换。

- 官方模型：`Qwen/Qwen3-VL-4B-Instruct-GGUF`
- 权重版本：`625828cbd3489786522366e19e22b6f12278e41a`
- 语言权重：`Qwen3VL-4B-Instruct-Q4_K_M.gguf`，2,497,281,664 字节
- 视觉权重：`mmproj-Qwen3VL-4B-Instruct-F16.gguf`，836,180,256 字节
- 路径：`/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent/models/qwen3-vl-4b-gguf/`
- 运行时：`/home/radxa/llama.cpp/build/bin/llama-server`，8323 / `57819b8d4`
- `8032` 读表/步骤检查配置：CPU、8 线程、4096 上下文、1 个并发请求。`8034` 器材服务另用 NPU 编码器与 CPU 语言，固定 320 输入。

## 下载和校验

`scripts/download_qwen3vl4b_gguf.py` 从 ModelScope 的 Qwen 官方仓库按固定版本下载。
分块文件支持中断后继续，完整文件通过发布者 SHA-256 校验后才更名为可加载的 `.gguf`。
下载期间不要把局部分块当作已部署模型。

```bash
cd /home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent
python3 -u scripts/download_qwen3vl4b_gguf.py
journalctl --user -u labagent-qwen3vl4b-download -n 30 --no-pager
```

## 服务与接口

`labagent-qwen3vl4b.service` 运行模型，监听 `127.0.0.1:8032`，支持 OpenAI 风格的 `/v1/chat/completions` 图片请求。
`scripts/qwen3vl_gguf_service.py` 将它适配为项目所需的 `/health`、`/inspect`、`/locate_display`、`/recognize`。
部署测试期间适配器监听 `127.0.0.1:8033`；现已通过真实模型检查，通过 systemd 覆盖文件更新 `labagent-demo-vlm.service` 并沿用 `8031`。

适配器运行在 `LabAgent-demo` 工作目录，复用该项目的 `labagent.readings` 与 `labagent.vision_regions`。
它使用 `.venv-app/bin/python`，无需 MNN 或外部大模型 API Key。

```bash
systemctl --user status labagent-qwen3vl4b labagent-demo-vlm --no-pager
curl http://127.0.0.1:8032/health
curl http://127.0.0.1:8031/health
journalctl --user -u labagent-qwen3vl4b -n 40 --no-pager
```

沿用 `8031` 时无需修改 `.env` 的 `LABAGENT_OCR_BASE_URL=http://127.0.0.1:8031`。
该接口同时处理器材和读表；`.env` 中旧的 `mnn` provider 名称仅用于兼容现有项目协议，实际后端以 `/health` 的 `backend` 为准。

器材框由模型输出的 0–1000 坐标转换成前端的归一化 `xywh`；无效框不会当作有效观察。
“红黑表笔”会拆为红、黑两支分别定位，并对候选框进行局部放大复核，检查尖头、握柄和颜色。
只有两支都经过复核，才标记组合项齐全；插头或无法确认的候选不视为已确认的表笔。
单次器材观察含复核的总推理等待预算为 220 秒，外层项目 HTTP 超时为 240 秒。
读数仍为待人工确认的候选值。没有可见单位时返回 null，由已确认的档位提供单位。

## 真实模型验证

使用 `scripts/verify_qwen3vl4b.py`，对已有器材照片做三次识别、一次空白反例、一次红黑表笔分别识别、一次裁掉黑色手持表笔的反例，以及数字不可辨认显示屏的读数反例。
裁剪反例的坐标只适用于这里指定的 1920×1080 参考照片，不应套用到其他测试照片。
测试直接请求视觉适配器，不创建实验会话、不写入测量或报告。

```bash
cd /home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent
../LabAgent-demo/.venv-app/bin/python scripts/verify_qwen3vl4b.py \
  --base http://127.0.0.1:8031 \
  --image ../LabAgent-demo/data/images/4017346abd93f9916a9f8b1e009be3fb0eb9f1beaffff8ca06340a21ab570218.jpg \
  --output ../LabAgent-demo/.runtime/qwen3vl4b-recheck.json
```

这些检查只反映该固定照片及空白反例的行为。不同角度、光照、遮挡和缺少单件器材仍需实拍验证；CPU 量化部署的性能不能用在线完整精度服务的结果替代。

2026-10-30 的最终检查记录位于开发板 `LabAgent-demo/.runtime/qwen3vl4b-validation-checked.json`：
三次完整器材图均返回电池、万用表和两支表笔的有效框；空白图均返回不存在；红黑分项均通过；
缺少黑色手持表笔时组合项返回未确认（null）；无法辨认数字的屏幕返回 null 读数。
原始单次全图推理曾把黑色插头当作表笔，不能仅凭全图模型的检测框判定可靠；最终方案通过局部复核拒绝了该候选。

正式 `8031` 接口在模型重启后的完整器材测试中耗时 57.60 秒，返回电池、万用表与两支表笔共 4 个有效框，记录为 `LabAgent-demo/.runtime/qwen3vl4b-deployed-smoke.json`。
同图重复请求可能使用缓存，不能据此承诺每一帧的延迟。该测试没有验证可见数字的读数准确率，需要另行拍摄清晰的实际读数测试。

## 回退

接入前先备份原 `labagent-demo-vlm.service`，再通过 `labagent-demo-vlm.service.d/qwen3vl4b.conf` 单独覆盖启动命令。
需要恢复旧视觉后端时，可将该覆盖文件移出 `.d` 目录，执行 `systemctl --user daemon-reload` 和 `systemctl --user restart labagent-demo-vlm`，确认旧服务健康后停止 `labagent-qwen3vl4b`。
实验数据、原 MNN 权重和原启动脚本不需要改动。
