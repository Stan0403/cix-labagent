# O6 液晶读数识别微调

这个目录保存训练、离线验证与部署证据。2026-11-22 已按用户要求将第三轮模型接入正式读表，
使用 CPU ONNX 优先、Qwen3-VL 回退；正式实验记录未用于测试。原始模型来自
[CIX AI Model Hub 26 Q2](https://modelscope.cn/models/cix/ai_model_hub_26_Q2)，
修订 `f0ee7fef80bd137483da2d988a1c4a2813066036`，路径为
`models/ComputeVision/OCR/onnx_PP_OCRv4/model/rec.onnx`。
模型 SHA-256：`ddc1eb79a3c35d9ef894c877510560d016440bb512d96da3f58d464a65df5063`。

## 2026-11-22 新照片训练与正式接入

用户新采集的 81 张原始照片来自同一批次 `20261122_191101_3fbaeb8d`，已逐张核对，
包括 51 种读数字符串、34 张负值；全部加入训练，没有从这一批中宣称独立测试准确率。
原照片和采集记录保持不变，审阅标签另存于批次的 `reviews/manual-review-user81.jsonl`。

第三轮从第二轮最佳权重初始化，再训练 1200 步，约 326 秒；实拍训练裁剪共 129 张。
产物：`runs/lcd-v3-user81/rec-lcd.onnx`，SHA-256：
`ec0b3f0b6090c41720d1bcccc940103fffce74b6db5d1aa044229d40e8655d35`。

| 检查项 | 第三轮训练前 | 第三轮训练后 |
| --- | --- | --- |
| 新 81 张训练图的拟合 | 66/81 | 81/81 |
| 400 张合成验证图整串正确 | 332/400 | 341/400 |
| 旧 4 张数字诊断图整串正确 | 2/4 | 3/4 |
| 旧 4 张空屏返回空串 | 4/4 | 4/4 |

旧诊断图 `candidate-006.png` 的正确标签是 **`.419`**，此前人工误标为 `4.19`。
这张图未用于梯度更新、校准或检查点选择，模型仍输出错误的 `41.9`。
原始清单与历史报告保留以便追溯，修正记录见 `annotation-corrections.json`、
`data/real*-corrected.jsonl` 和各轮 `corrected-diagnostic.json`。
`annotate_reviewed.py` 的后续生成标签已经修正。

正式流程增加自适应显示屏定位、源像素小数点/负号检查和高置信度双重采样检查。
OCR 无法确认时回退 Qwen3-VL；两个模型的数字位不同则拒收，像素检查不据此补数字。
空屏返回未知，不作为零或关机。量程、证据时效及两帧稳定规则继续由业务流程校验。

O6 在这 81 张训练照片上运行“定位＋识别”库调用，中位耗时约 0.294 秒、P90 约 0.520 秒；
78 张走快速路径，3 张回退。这只是开发样例的路由与延迟检查，不能当作独立场景准确率。
旧 VLM 对单张读数约 3.35 秒。正式 HTTP 检查、已知错误的拒收和回退结果见
`production-reader/`；部署配置、回退步骤见 `../LabAgent-demo/docs/LCD_READER.md`。

第三轮已准备 193 张训练来源校准图和 `recbuild.cfg`；没有可用 `cixbuild`，新模型尚未编译为
`.cix`，**当前正式读表使用 CPU，不是 NPU**。

## 前两轮历史结果

两轮训练已完成：第一轮 1000 步，第二轮恢复后训练到总计 3000 步；
第二轮按合成验证成绩选中第 2800 步，产物为 `runs/lcd-v2/rec-lcd.onnx`。

| 检查项 | 原始模型 | 第一轮 | 第二轮最佳模型 |
| --- | --- | --- | --- |
| 400 张合成验证图整串正确 | 5/400（1.25%） | 274/400（68.5%） | 332/400（83%） |
| 4 张实拍数字诊断图整串正确 | 0/4 | 1/4 | 2/4 |
| 4 张额外实拍空屏返回空串 | 0/4 | 4/4 | 4/4 |

第二轮实拍数字结果：

| 人工标注 | 模型字面输出 | 结果 |
| --- | --- | --- |
| `1.34` | `1.34` | 正确 |
| `1.353` | `1.3.53` | 小数点重复 |
| `.419`（修正标签） | `41.9` | 小数点移位 |
| `-0.00` | `-0.00` | 正确 |

这批实拍诊断图太少，且已在开发中查看，不能把 2/4 当作真实总体准确率。
当时的第二轮候选存在小数点问题，没有接管正式读表。
ONNX 导出与 PyTorch 数值对齐通过，四张实拍上的最大绝对输出误差约 `6.5e-6`；
导出的算子拓扑与原 CIX 图一致。
已准备 112 张仅来自训练集的 NPU 校准图和编译配置，尚未编译、运行新的 `.cix`。
详细记录见各轮 `report.json`、`blank-diagnostic.json`、`npu-preparation.json`，
汇总见 `training-summary.json`。该历史阶段未替换原始通用模型、正式服务或实验记录。

## 训练范围

CIX 目录命名为 PP-OCRv4，但这份识别图实际使用 ResNet34-vd、双层双向 LSTM、
6625 类 CTC；它与 PaddleOCR 官方轻量 PP-OCRv4 的 SVTR_LCNet 网络不同。
本次直接恢复该 ONNX 的浮点权重训练，没有换成另一个模型。
PyTorch 恢复网络与原始 ONNX 的数值对齐见 `parity.json`。
结构参考 PaddleOCR release/2.7 的 `rec_resnet_vd.py`，原源码与 Apache-2.0 许可证保存在
`assets/PaddleOCR-release-2.7.zip`。模型许可证在 `assets/MODEL-LICENSE.txt`。

训练 CTC 分类层和最后一组 CNN 残差块，冻结 LSTM、其余 CNN、所有 BN 统计。
字符表、输入预处理、原 ONNX 算子与节点名称均保留。训练后把 CNN/CTC 权重写回
原始 ONNX 图；导出再次对比 PyTorch 和 ONNX 输出，失败则中止。
这是液晶数字专用适配，不能据此保证通用中文 OCR 能力。

## 数据与验收边界

新增照片可以在 O6 桌面双击“液晶数据采集”，打开摄像头后按空格保存。
拍摄结果在桌面“液晶采集照片”文件夹快捷方式下，按批次保存原分辨率照片与 JSON 标签记录。
默认待标注，数字、空屏、看不清和超量程分类保留；所有记录先标为待核对，再进入训练。
操作及数据格式见 `../LabAgent-demo/docs/LCD_CAPTURE.md`。

- 4000 张合成训练图，400 张合成验证图，标签来自七段数字渲染器。
- 第一轮 44 张人工核对的实拍训练裁剪，包含 1.34、0.00、-0.00 和无数字的屏幕。
- 第二轮增加 4 张人工裁剪的实拍训练图，按可见字符标成 `1359`、`0.89`、`00.3`、`.001`。
  不根据电池或量程推断、插入小数点。第二轮共 48 张实拍训练图，另加入保留字符的旋转增强。
- 4 张源目录分离的诊断留出图：1.34、1.353、.419、-0.00（`.419` 标签已修正）。
- 另有 4 张人工审阅的空屏诊断图（253、325、338、341），不参与训练、校准或 checkpoint 选择。
  空屏指标单独记录在 `blank-diagnostic.json`，不与四张数字图混算准确率。
- 9 张候选裁剪因数字、小数点不完整等原因没有纳入。
- 留出照片与训练照片使用相同仪表和相机，采集批次独立性尚未证实。
  这 4 张图在开发中反复用于诊断，不属于最终未查看的测试集，不能代表真实场景总体准确率。

`data/real.jsonl` 保留源文件 SHA、裁剪 SHA、人工标签与分组。
训练前校验训练集和验证/留出集没有相同图像 SHA。
合成验证精度仅用于选择 checkpoint；真实留出图不用于梯度更新、量化校准或 checkpoint 选择。
最终报告分开记录合成验证、实拍留出和实拍训练拟合结果。
无数字的屏幕标签为空串，不标成零；小数点、负号保留原样。

## 在本机复现

本次使用 Windows、Python 3.10、GTX 1650 Ti 4 GB、PyTorch 2.10.0+cu130。
`.venv-torch` 通过 `--system-site-packages` 复用电脑已经验证的 PyTorch CUDA 环境，
其他依赖隔离安装，版本见 `requirements-torch.txt`。
尝试的 Paddle 2.6.2 GPU 环境缺少 cuDNN 8 DLL，未用于训练；不需要它来复现本流程。

从 `E:/codex_projext/cix-agent` 执行：

```powershell
& ocr_training/.venv-torch/Scripts/python.exe ocr_training/fetch_sources.py
& ocr_training/.venv-torch/Scripts/python.exe ocr_training/build_dataset.py
& ocr_training/.venv-torch/Scripts/python.exe ocr_training/annotate_reviewed.py
& ocr_training/.venv-torch/Scripts/python.exe ocr_training/cix_rec_model.py
& ocr_training/.venv-torch/Scripts/python.exe ocr_training/train_lcd.py --steps 1000
& ocr_training/.venv-torch/Scripts/python.exe ocr_training/prepare_npu.py
```

第二轮使用同一合成训练/验证集，恢复第一轮权重继续训练：

```powershell
& ocr_training/.venv-torch/Scripts/python.exe ocr_training/annotate_extra.py
& ocr_training/.venv-torch/Scripts/python.exe ocr_training/train_lcd.py --steps 3000 --resume ocr_training/runs/lcd-v1/latest.pt --output runs/lcd-v2 --real-manifest real-v2.jsonl --geometry
& ocr_training/.venv-torch/Scripts/python.exe ocr_training/prepare_npu.py --run runs/lcd-v2
& ocr_training/.venv-torch/Scripts/python.exe ocr_training/evaluate_blank.py --run runs/lcd-v2
```

第二轮新增图依赖 `data/extra/` 的四张源照片与 `sources.json`。
第一轮 checkpoint 早于随机状态保存功能，首次恢复会明确提示采样器从固定种子重启；
后续 checkpoint 会保存采样器与 Torch 随机状态。继续训练使用同一输出目录时应传 `--resume`，
或选择新的 `--output`，避免覆盖已有实验。

`annotate_reviewed.py` 依赖本次人工审阅的 `data/real/candidates.json` 和裁剪图，
不是任意新照片的自动标注器。不能把另一批图片沿用这些数字 ID 的标签。
重新采集的数据必须重新核对标签、按采集批次分组后划分训练与留出集。

结果在 `runs/lcd-v1/` 与 `runs/lcd-v2/`：`best.pt`、可恢复训练的 `latest.pt`、`rec-lcd.onnx`、
`report.json`、`history.json`、`rec_cal.npy`、`recbuild.cfg`。
合成图和原始模型可重新生成/下载；相机图属于项目本地数据，不提交到公开仓库。

## NPU 转换

`prepare_npu.py` 仅用训练图生成量化校准集，并检查导出的算子拓扑与 CIX 原图相同。
在安装原 CIX SDK 的 Linux x86_64 环境，以 `ocr_training` 为工作目录执行：

```sh
cixbuild runs/lcd-v1/recbuild.cfg
```

第二轮对应命令为 `cixbuild runs/lcd-v2/recbuild.cfg`；第三轮为
`cixbuild runs/lcd-v3-user81/recbuild.cfg`。

O6 当前安装的是推理运行时，没有发现 `cixbuild` 编译器。
生成 ONNX 和编译配置不等于已生成可用的 `.cix`。
转换后必须在 O6 上重新验证 INT8/FP16 读数、小数点、负号、空屏拒识和延迟，
再决定是否接入正式读表路径。首轮少量实拍结果不足以自动替换正式 VLM。

浮点 ONNX 可以单独离线诊断，不需要训练框架：

```powershell
& ocr_training/.venv-torch/Scripts/python.exe ocr_training/infer_lcd.py ocr_training/data/real/candidate-005.png --model ocr_training/runs/lcd-v2/rec-lcd.onnx
```

该命令只运行 CPU ONNX，检查字面输出与数字语法，不会写实验记录。
语法合法不代表读数正确，也不建立量程、单位或置信度验收结论。

后续优先增加不同电压、负值、量程、角度、反光、模糊、未点亮和超量程的真实照片。
尤其要补齐四位数字与不同小数点位置，避免模型只记住当前的 1.34 和 0.00。
