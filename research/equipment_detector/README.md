# 实验器材小模型训练与 O6 NPU 部署

这是训练准备方案与四类配置，尚未采集完整数据集、启动训练或产出自训练权重。当前已有实拍样例不足以训练和独立验证可靠的器材检测器。正式项目的视觉服务保持原状。

目标：先让固定电池实验的器材检查使用小型检测器，识别电池、万用表、红表笔、黑表笔并输出框；Qwen3-VL 保留为未覆盖器材和复杂场景的后备。显示屏数值、档位、接触和电气状态是另外的任务。

## 1. 先做小规模可行性验证

模型沿用 CIX `ai_model_hub_26_Q2/models/ComputeVision/Object_Detection/onnx_yolov8_n` 选定的 YOLOv8n 路线，从其官方原始预训练权重微调。CIX 条目指向 Ultralytics v8.1.43；训练、PyTorch、ONNX 导出、CixBuilder 和板端 UMD 的实际版本应记录并固定。

先标注约 50–100 张有差异的图片，用于跑通训练 → ONNX → CIX 编译 → NPU 推理 → 项目接口。它们只是小规模流程验证，不是准确率验收集。先验证工具链，再投入大量标注。

本机已识别到 GTX 1650 Ti / 4096 MiB。可从 YOLOv8n、640 输入、batch=2 开始微调；显存占用和耗时需实际测试，不假定任何固定训练时间。

CIX 编译使用与板上驱动/UMD兼容的 Linux x86_64 SDK。Radxa 当前公开 SDK 示例要求 Python 3.10。可在电脑上准备 Ubuntu 环境；如选 WSL2，应先验证 SDK 是否可正常运行。开发板只负责最终 NPU 推理，本方案不在 O6 上进行训练。

## 2. 拍摄与标注

固定四类，与 `data.yaml` / `labels.json` 的顺序一致：

| ID | 类别 | 标注范围 |
|---:|---|---|
| 0 | battery | 当前实验要识别的电池本体；不包含导线和手 |
| 1 | multimeter | 万用表本体；不把整片桌面或表笔导线框进去 |
| 2 | red_probe | 红表笔的金属尖端与绝缘手柄；不包括尾部导线、插在表上的香蕉插头 |
| 3 | black_probe | 黑表笔的金属尖端与绝缘手柄；同上 |

使用实际 O6 摄像头和演示机位，拍摄不同摆放、距离、角度、光照、背景、手部干扰及轻度遮挡。必须包含只有单支表笔、缺电池、缺万用表、空桌面、相似颜色导线和无关物体等反例。

严重遮挡、模糊到人也无法判断的物体，不要凭上下文猜类别；先标记为待审并统一处理，不能一部分漏标、一部分推断标注。对清晰可见的目标应完整标注，不能只标图中一个物体而遗漏其他目标。

可以录短视频抽帧，但应去除近重复帧。后续可从约 500–1500 张有差异的图片起步，根据独立测试的漏检/误检继续补拍；这是采集规模建议，不是准确率保证。

可用 CVAT 画矩形，导出 **Ultralytics YOLO Detection**。每张图片对应同名 `.txt`，每个物体一行：

```text
class_id center_x center_y width height
```

坐标均为相对于图片的 0–1 比例。确实没有目标的负样本保留图片与空标签文件。

按拍摄批次、视频或场景分组后，再划分约 80% 训练、10% 验证、10% 测试。不要把同一视频的相邻帧随机分到训练集和测试集；验证集用于调阈值，测试集留作最终评估。

```text
dataset/
  images/train/    labels/train/
  images/val/      labels/val/
  images/test/     labels/test/
```

## 3. 微调与 ONNX 导出

下列为环境准备后使用的命令示例；目前没有数据集和 `best.pt`，不能作为已完成训练的命令记录。先核对安装版本与 CUDA 可用性，Windows 训练用小批量和 workers=0 起步。

```powershell
cd E:\codex_projext\cix-agent\equipment_detector
yolo detect train model=yolov8n.pt data=data.yaml imgsz=640 epochs=80 batch=2 device=0 workers=0 project=runs name=equipment_v1
yolo detect val model=runs/equipment_v1/weights/best.pt data=data.yaml split=test imgsz=640 device=0
```

导出固定 batch=1、固定 640×640、FP32、原始检测输出的 ONNX。可从 opset 12 起做编译兼容验证，最终以所用 SDK 的支持情况固定参数，而非盲用导出器的最新默认值：

```powershell
yolo export model=runs/equipment_v1/weights/best.pt format=onnx imgsz=640 batch=1 dynamic=False half=False simplify=False opset=12
```

本方案不在 ONNX 中嵌入 NMS。标准四类 YOLOv8 detect 在该输入下，应检查输入 `[1,3,640,640]`、输出 `[1,8,8400]`。这与 CIX 原 80 类 `[1,84,8400]` 不同，必须同时修改推理后处理和类别清单。

先在同一组图片上比较 PyTorch 与 ONNX 的框、分数和类别，再继续 NPU 编译。导出成功不等于结果一致。

## 4. 校准并编译为 CIX 模型

从训练数据中挑选覆盖光照、目标尺寸、遮挡和反例的代表性图片制作校准数据，不使用最终测试集。校准预处理必须与部署时一致：RGB、等比例 resize/letterbox、padding、float32 NCHW、归一化；保存 padding/缩放参数用于框坐标还原。

以 CIX 26_Q2 的 `cfg/yolov8_nbuild.cfg` 为基础，修改模型路径、实际输入输出名、校准数据路径和输出文件名。目标为本板的 `X2_1204MP3`，然后使用匹配版本的 SDK：

```bash
cixbuild cfg/equipment_yolov8n.cfg
```

不能直接原样复制原配置：其 `trigger_float_op` 使用特定节点编号指定混合精度。重新导出或更换类别后，应核对实际图节点，重新验证精度，不能假设这些编号仍对应相同算子。

先建立浮点参考，再测试 INT8 或混合精度。编译为 `.cix` 后，在独立测试集上比较 PyTorch、ONNX 与 NPU 的输出与检测指标；发生明显精度下降时继续调整校准/精度配置，不能仅以模型能加载作为验收。

## 5. 接入当前项目

当前新版器材观察由 `LabAgent-demo/labagent/demo_api.py` 的 `/api/sessions/{id}/observe`、`task=objects` 请求视觉服务 `/inspect`。旧 `Perception._detect_onnx` 可参考预处理、NMS 和框坐标还原，但不能只改 `LABAGENT_VISION_PROVIDER=onnx` 就让新版网页使用它。

需要新增常驻 CIX 检测后端，复用已安装的 `NOE_Engine.EngineInfer`，只在启动时加载模型。对 `/inspect` 返回当前协议的 `present`、`boxes`、`coordinate_space`、`backend`、`requires_confirmation`：

- `battery`、`multimeter` 直接对应类别。
- 红、黑表笔分别检测；只有两者均满足验证条件时，才把项目的 `probes` 标记为齐全，保留两只框。
- 低分、遮挡或未检出时不自动勾选，不把“未检出”当成物体绝对不存在。
- 对不在四类清单中的新实验器材明确转交 VLM 或人工核对，不能把新名字直接套成已有类别。
- 读表、档位和接触检查保留现有链路；四类检测器不会读取 LCD 数值或证明电气状态。

若要实时画框，需要单独的轻量预览检测通道：只保留最新一帧，忙时丢弃旧帧；结果稳定或人工确认后才保存证据。不能把现有会写图片、审计和会话版本的 `/observe` 每秒调用几十次。最终器材确认流程继续保留。

## 6. 验收与迭代

至少检查每类 precision/recall、检测框 mAP、设备组级误判（尤其缺黑表笔时是否误报齐全）、模糊遮挡下的拒绝行为，以及完整相机 → 预处理 → NPU → NMS → 网页的 p50/p95 延迟。确认实际 `/dev/aipu` 推理，并测试与 SenseVoice 同时使用 NPU 时的表现。

实时预览可以先以约 5 次/秒更新作为开发目标，是否达到须以完整链路实测为准。不得用官方 COCO 模型的裸推理时间替代本项目端到端 FPS。

下一步需要人工摆放器材、拍摄多样场景并审核四类框标注。抽帧、去重、分组划分、训练、导出、校准、编译、服务适配和评测可以由开发工具完成。

参考：[CIX 26_Q2 YOLOv8n 目录](https://modelscope.cn/models/cix/ai_model_hub_26_Q2/tree/master/models/ComputeVision/Object_Detection/onnx_yolov8_n)、[Radxa NPU 部署](https://docs.radxa.com/en/orion/o6/app-development/artificial-intelligence/Vision/yolov8n)、[CIX SDK 环境要求](https://docs.radxa.com/en/orion/o6/app-development/artificial-intelligence/env-setup)、[Ultralytics 训练](https://docs.ultralytics.com/modes/train/)、[Ultralytics 导出](https://docs.ultralytics.com/modes/export/)、[CVAT YOLO 标注格式](https://docs.cvat.ai/docs/dataset_management/formats/format-yolo-ultralytics/)。
