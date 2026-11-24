# 器材识别的 NPU 部署与实测

日期：2026-11-17。已按用户要求启用正式器材检查的 NPU 视觉编码 + CPU 语言链路。网页仍请求 `8031/inspect`，适配器把器材整图和表笔局部复核发往 `8034` 混合模型；读表、档位、接触、收尾与显示屏定位继续使用 `8032` CPU 模型。语言生成仍在 CPU。

## 正式服务与验收

| 服务 | 作用 |
|---|---|
| `labagent-demo-npu-vision.service` | 常驻 CIX NPU 视觉编码器，用户私有 Unix socket |
| `labagent-demo-equipment-vlm.service` | `127.0.0.1:8034`，NPU 视觉 + CPU 语言 |
| `labagent-demo-vlm.service` | `127.0.0.1:8031`，按任务分流，返回实际后端 |
| `labagent-qwen3vl4b.service` | `127.0.0.1:8032`，原 CPU 读表与步骤检查 |

两项新增服务已加入 `labagent-demo.target` 并启用，异常退出由 systemd 重启。源码、原单元与新增适配器 drop-in 的原状态已备份。未执行整机重启。

新增路由、宽高图坐标映射、padding 排除、缺黑表笔、CPU 读表/步骤路由和既有工作流相关测试共 53 项通过。真实模型预检：器材齐全 30.056 秒、白图 11.806 秒、缺黑表笔 30.385 秒，均通过；未亮屏由 CPU 返回空读数，未增加 NPU 请求计数。

正式 `8031` 接口与真实业务 `/observe` 的完整链路在隔离临时 replay 数据库中验证通过，器材齐全场景 29.892 秒。确认 NPU 编码进程持有 `/dev/aipu`，实际处理三个编码请求且错误为零；观察响应和审计均记录混合后端，仍停留在器材待确认步骤，没有创建正式测量。所有七项正式服务 active、六个健康接口返回 200。

`scripts/qwen3vl_gguf_service.py` 将原图等比例缩放补边为固定输入；输出框去除 padding 后映射回原图比例。表笔局部复核继续保留，器材最终确认仍由用户提交。

## 官方视觉编码器

递归读取 CIX 26_Q2 模型库后，在 `models/MultiModal/Qwen3-VL-4B-Instruct/` 子目录发现官方 `vision.cix`、`inference_npu.py`、浮点 ONNX 及构建配置。顶层同名 Markdown 主要介绍 CPU/GPU，不能据此认定整个目录没有 NPU 方案。

- 固定修订：`f0ee7fef80bd137483da2d988a1c4a2813066036`。
- `vision.cix`：838,765,852 字节，SHA-256 `fd315596a7028cbc649be09576fbe885ec165ae92881df394fd0036869d7e4d0`。
- 固定输入：`[400,1536]`，对应 `320×320` 图像、`1×20×20` patch grid。
- 四路输出各为 `[100,2560]`，分别是主视觉特征与三路 deepstack 特征。
- 官方方案用 NPU 替换视觉 forward，语言模型仍由另一运行时生成文本；不是整个 Qwen3-VL 在 NPU 上生成回答。

权重与参考文件按仓库元数据校验 SHA-256，保留官方许可证，未重新量化。NPU 独立探针确认打开 `/dev/aipu`、四路结果尺寸正确且有限；三次编码分别为 0.460、0.464、0.457 秒，模型加载约 0.365 秒。这不代表完整器材识别只需半秒。

## 独立混合原型

在 `/home/radxa/llama.cpp-codex-npu` 建立固定提交 `57819b8d4b39d893408e51520dff3d47d1ebb757` 的隔离 worktree，原正式源码与运行时保持原状。

原型在 `clip_image_batch_encode` 处通过仅本用户可访问的 Unix socket 请求 Python NOE worker，沿特征维拼接四路输出，CPU 继续处理现有 Q4_K_M 语言权重。只有显式设置 `LABAGENT_NPU_VISION_SOCKET` 才启用；形状或通信错误时拒绝推理，不静默声称 NPU 成功。

测试图先等比例缩放并补边为 `320×320`，表笔复核裁剪也使用相同尺寸。CPU 对照与 NPU 使用同样的 100 个图像 token、八个 A720 核、相同提示和现有表笔局部复核。测试仅请求模型，不创建实验会话或测量。

| 测试 | CPU 固定 320 | NPU 视觉 + CPU 语言，固定 320 | 断言 |
|---|---:|---:|---|
| 器材齐全，含表笔局部复核 | 34.971 秒 | 29.578 秒 | 电池、万用表、双表笔存在且有框；通过 |
| 白图 | 13.418 秒 | 11.675 秒 | 不报告器材；通过 |
| 未亮显示屏 | 6.795 秒 | 5.043 秒 | 不编造数字；通过 |
| 缺黑表笔 | 34.644 秒 | 29.519 秒 | 不认为双表笔齐全；通过 |

器材齐全场景同分辨率耗时降低约 15.4%。原正式动态分辨率的同场景约 57.509 秒；它与 29.578 秒之间的差异还包含缩小输入的收益，不能全部归功于 NPU。每种候选仅测一次、只有一张器材实拍图及其派生反例，不构成准确率统计。

## 数值兼容性与启用范围

将同一份、由 GGUF 预处理得到的归一化像素分别送入 CPU GGUF 视觉编码器和 NPU，再比较四路特征：余弦相似度约为 0.897 / 0.720 / 0.928 / 0.923，归一化误差约 0.467 / 1.259 / 0.387 / 0.418。与本次数值一致性检查的门槛不符，四路交叉比较也未发现简单的输出顺序错位。

前期严格数值对照的原始记录仍保留 `eligible_for_promotion=false`，该字段不会因部署而改成数值一致。用户在了解上述限制后明确要求把正式器材检查切换到部分 NPU 方案；本次按其要求启用，并另做正式路由、坐标、反例与业务链路验收。功能检查通过并不解释这些数值差异。

已进一步下载并校验同目录的浮点 `vision.onnx`（1,661,882,977 字节，SHA-256 `a7827c989e2f99d319c7e26d496afce39c4e39203e304277881f73aa3d124dd1`），用相同 patch 输入在本板 ONNX Runtime 1.31.1 CPU 上对照，单次 forward 约 1.186 秒：

| 特征流 | NPU 对浮点 ONNX：余弦 / 归一化误差 | GGUF 对浮点 ONNX：余弦 / 归一化误差 |
|---|---|---|
| 主特征 | 0.917 / 0.406 | 0.952 / 0.306 |
| deepstack 0 | 0.987 / 0.160 | 0.742 / 0.693 |
| deepstack 1 | 0.953 / 0.307 | 0.964 / 0.267 |
| deepstack 2 | 0.945 / 0.343 | 0.960 / 0.280 |

这说明差异也存在于 NPU 编译模型与同来源的浮点参考之间，不能仅归因于 HTTP 接口或四路输出排列。具体原因尚未完全确定，仍需继续核对编译数值精度和 GGUF 视觉实现/版本，并用更多实拍数据评测。当前启用的是器材观察服务，不宣称编码器与浮点模型数值等价或已经完成全面准确率验收。

固定 320 输入会减少细小表笔、插孔文字、屏幕负号等细节，因此读表和接线相关检查继续使用原有分辨率与 CPU 服务。器材识别错误仍可人工修正。NPU worker 不可用时器材请求会报错，不静默标注为 NPU 成功。

## 配置与回退

适配器覆盖文件为 `~/.config/systemd/user/labagent-demo-vlm.service.d/equipment-npu.conf`，设置 `LABAGENT_EQUIPMENT_VLM_SERVER=http://127.0.0.1:8034`。固定网格运行时为 `/home/radxa/llama.cpp-codex-npu/build/bin/llama-server`。官方编码权重安装在项目 `models/qwen3-vl-4b-npu/`，保留许可证、版本和 SHA 清单。

日常检查：

```bash
systemctl --user status labagent-demo-npu-vision.service labagent-demo-equipment-vlm.service --no-pager
curl --fail http://127.0.0.1:8031/health
```

需要将器材检查恢复 CPU 时，移除本次新增的路由覆盖并关闭新增服务即可，读表和实验数据无需回退：

```bash
rm -- "$HOME/.config/systemd/user/labagent-demo-vlm.service.d/equipment-npu.conf"
systemctl --user daemon-reload
systemctl --user restart labagent-demo-vlm.service
systemctl --user disable --now labagent-demo-equipment-vlm.service labagent-demo-npu-vision.service
curl --fail http://127.0.0.1:8031/health
```

完整源码与配置原状态在板端 `/home/radxa/.local/state/codex-model-accel/20261117/production-npu/backup/`。以后已有其他开发变更时，优先使用上述配置回退，避免覆盖后续源码。

## 文件与复现位置

本地原始数据在 `E:\codex_projext\cix-agent\model_acceleration\2026-11-17\`：

- `multimodal-catalog.json`、`npu-vision-download.json`：目录、版本和权重校验。
- `vision-candidate-source.log`：已校验的官方参考源码。
- `npu-vision-probe.json`：真实 NPU 编码时间与设备句柄。
- `npu-bridge-build.json`：隔离构建结果。
- `npu-equipment-benchmark.json`：数值差异与完整逐项识别输出。
- `npu-onnx-reference-check.json`：同输入的 NPU / GGUF / 浮点 ONNX 对照。
- `production-npu/deployment.json`、`formal-results.json`、`preflight-results.json`、`unit-tests.log`：正式启用与验收。

开发工具在本地 `tools/o6_*npu*`；板端测试与备份根目录 `/home/radxa/.local/state/codex-model-accel/20261117/`。独立测试端口 18042/18043 已释放，正式混合服务使用 8034。没有修改原 CPU 模型 8032 的启动配置。

对于更低延迟的固定器材检测，可以采用 CIX 模型库内的 YOLO 路线，但需要匹配电池、万用表和表笔的训练类别。板上当前未发现相应自训练权重，普通 COCO 模型不能直接替代。不能在未测本项目模型时套用通用 YOLO 的 FPS。

参考：[CIX NPU 视觉子目录](https://modelscope.cn/models/cix/ai_model_hub_26_Q2/tree/master/models/MultiModal/Qwen3-VL-4B-Instruct)、[CIX NPU 视觉说明](https://modelscope.cn/models/cix/ai_model_hub_26_Q2/resolve/master/models/MultiModal/Qwen3-VL-4B-Instruct/ReadMe.md)、[Radxa YOLOv8n NPU 部署](https://docs.radxa.com/en/orion/o6/app-development/artificial-intelligence/Vision/yolov8n)。
