# 正式液晶读表后端

2026-11-27更新：当前按键单帧采集通过fast_only请求快速定位和OCR，不确定时返回未知，不调用VLM回退。普通识别与旧接口仍可使用下文的通用回退路线。新入口按计划范围保留实际符号与数值，并将报告来源标为单帧识别；旧两帧稳定性描述不代表该入口已做稳定性验证。

2026-11-22 已按用户要求启用第三轮微调识别器。网页入口仍为 O6 本机
`http://127.0.0.1:8001`。当前新识别器使用 **CPU ONNX Runtime**，没有使用新 NPU 模型。

## 处理链路

1. 网页拍摄 → 主服务 `perform_observation`。
2. `8031/locate_display` 在检测到的万用表内自适应定位 LCD，兼容这批偏暗的屏幕。
   快速定位无结果时使用原定位路线；没有固定相机坐标。
3. 主服务从原分辨率照片裁剪显示屏，以 PNG 发送到 `8031/recognize`。
4. 微调 PP-OCR 识别数字；源像素检查数字数量、小数点和负号。
   图像几何不明确时，仅允许高置信度且两种缩放采样结果一致的数字走快速路径。
5. 未通过的读数回退 Qwen3-VL。若两种模型的数字位不同，则返回未知；
   小数点或负号的差异需要明确像素依据，不能凭电池电压或量程补点、补位。
6. 主服务执行既有单位、已确认量程、证据时效和稳定读数规则，保留真实后端与原图引用。

OCR 空串返回未知，不等于零，也不能证明仪表关机。单独的 `1`、`OL`、重复小数点和无法确认的结果
不会作为快速有效读数。用户可调整相机后重试或人工确认。

这次接入没有改变两帧稳定后自动记录、人工确认、负值提示等业务规则。
实际结果的 `backend` 是 `ppocr-lcd-v3-user81-onnxruntime-cpu` 或
`qwen3-vl-4b-q4_k_m-llamacpp-cpu`，不会把回退结果标成 NPU。

## 模型和配置

板端项目根目录：
`/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo`。

- 权重：`data/ocr-training/20261122-user81/rec-lcd.onnx`。
- 字典：同目录 `ppocr_keys_v1.txt`，保留 CIX 的 6625 类输出。
- 权重 SHA-256：`ec0b3f0b6090c41720d1bcccc940103fffce74b6db5d1aa044229d40e8655d35`。
- 运行时：`.venv-app` 内 ONNX Runtime，强制 `CPUExecutionProvider`，4 个计算线程。
- systemd 覆盖：`~/.config/systemd/user/labagent-demo-vlm.service.d/30-lcd-reader.conf`。
- 配置项：`LABAGENT_LCD_OCR_MODEL`、`LABAGENT_LCD_OCR_DICT`、
  `LABAGENT_LCD_OCR_SHA256`、`LABAGENT_LCD_OCR_THREADS`。

加载失败会记录错误并保留旧 VLM 回退；健康接口会明确显示 `reading_ready=false`。
`/health` 中的 `default_backend` 仍是通用视觉模型，读表请查看 `reading_backend`：

```sh
curl -fsS http://127.0.0.1:8031/health | python3 -m json.tool
curl -fsS http://127.0.0.1:8001/api/health
```

正常应包含 `reading_ready: true`、上述 `reading_backend` 和权重摘要。
网页健康接口的 `ocr_provider=mnn` 是沿用的 HTTP 协议配置名，不表示此模型运行在 MNN 或 NPU。

## 本次验证和耗时

- 本地 89 项相关回归检查通过，覆盖接口、读数解析、器材路线和业务工作流。
- O6 正式 8031 接口 14 项检查通过，包含 6 张完整场景、4 张旧数字裁剪和4张空屏。
  覆盖正确快读、VLM 回退、前置小数点和错误拒收。
- 另用隔离数据库执行实际网页 `/observe` 代码，经真实 8031 模型服务验证快读、漏位拒收、
  前置小数点回退三条业务链路。前置人工步骤仅为隔离测试模拟，不代表完成了物理实验。
- 正式 HTTP 的 3 张快速场景“自动定位＋读数”约 **0.28～0.53 秒**；
  本次回退场景约 **1.24～1.94 秒**。此前旧路线两次单图读数约 3.35 秒，
  提示词、缓存和生成长度会影响比较，不能把样例耗时当作固定上限。
- 81 张新照片的板端库调用：78 张快速接受、未出现错误快速接受，3 张回退；
  定位与识别中位约 0.294 秒、P90 约 0.520 秒，不含网页、HTTP 和业务记录开销。
- 正式接口曾暴露 `1.24` → `.24` 的回退漏位，已增加“两模型数字位不同即拒收”的检查并重测。
  `.419` 仍会被纯 OCR 读成 `41.9`，像素检查会阻止直接接受，并由 VLM 复核。

81 张照片全部参与训练，旧诊断照片也已在开发中查看；以上是功能和耗时检查，
**不是独立测试准确率**。后续应使用新批次、不同角度、量程、照明和模糊程度验证。
网页拍摄、Agent 决策和两帧稳定等待仍会增加用户看到结果的总时间。

本地证据在项目根目录 `ocr_training/production-reader/`；部署备份在板端
`~/.local/state/codex-lcd-reader/20261122-201222/`。
测试使用已有照片及隔离数据库，没有创建或修改正式实验记录。

## 回退本次接入

下面命令恢复本次部署前的适配器和读数解析，停用新增模型配置，再重启两项相关服务。
仅在需要回退时，在 O6 终端执行：

```sh
cd /home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo
LCD_BACKUP="$HOME/.local/state/codex-lcd-reader/20261122-201222"
cp "$LCD_BACKUP/backup-0" scripts/qwen3vl_gguf_service.py
cp "$LCD_BACKUP/backup-2" labagent/readings.py
mv "$HOME/.config/systemd/user/labagent-demo-vlm.service.d/30-lcd-reader.conf" "$LCD_BACKUP/disabled-30-lcd-reader.conf"
systemctl --user daemon-reload
systemctl --user restart labagent-demo-vlm.service labagent-demo-web.service
```

新权重、独立采集程序和原始照片保留；这些文件不影响恢复后的旧路线。
不要运行部署脚本来执行回退，该脚本校验部署前的源码摘要。

## 后续 NPU 转换

当前仅安装板端推理运行时，没有可用的 `cixbuild`。第三轮已经准备 193 张训练来源校准图
和编译配置，见项目根目录 `ocr_training/runs/lcd-v3-user81/`。
获得匹配的 CIX SDK 后，在支持的编译环境生成新 `.cix`，再验证量化后的数字、小数点、负号、
空屏拒识、并发及实际延迟，之后才能替换这里的 CPU 执行器。已有通用 OCR 的 NPU 文件
不包含本次训练后的权重。
