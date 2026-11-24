# CIX PP-OCRv4 本地 OCR

2026-11-22：正式 8031 读表已启用第三轮液晶微调识别器，使用 CPU ONNX、像素检查和 VLM 回退，
见 [正式液晶读表后端](LCD_READER.md)。本页后续为原通用 NPU OCR 的部署及历史诊断，
其中 `.cix` 不包含最新训练权重。

2026-11-17 诊断更新：已定位参考识别预处理将短文本强制拉伸至 `32×400`，导致字形失真。适配器现改为固定高度、等比例缩放、右侧补边，保留同一组 NPU 权重。两种字体的 12 个合成文本，原拉伸完整链路精确匹配 2/12，补边后 11/12；绕过检测器并给出完整文字区域时为 12/12。完整链路仍有负号被文本检测框裁掉的情况。原有三数字 HTTP 测试修正后识别对 `1.34` 和 `0.00`，但 `-0.12` 仍输出 `0.12`。此次修正未切换正式视觉/读表路由；还需要真实七段 LCD 标注集验证。诊断数据见本地 `model_acceleration/2026-11-17/ocr-diagnosis.json` 与 `npu-ocr-after-fix.json`。下面的实拍失败记录早于此次修正。

模型来自 CIX 26_Q2 模型库 `models/ComputeVision/OCR/onnx_PP_OCRv4`，检测、方向分类和识别三个 `.cix` 文件在 P1 NPU 上推理；图像预处理和文字解码在 CPU 上执行。模型文件和官方 `pp_ocr.py`、字典存放于 `models/ppocr-cix/`。独立 Python 环境为 `.venv-ocr`。

在项目目录启动：

```bash
bash scripts/start_local_ocr.sh
```

在 `.env` 配置：

```text
LABAGENT_OCR_PROVIDER=cix
LABAGENT_OCR_BASE_URL=http://127.0.0.1:8020
```

修改后重启主服务。使用 `curl http://127.0.0.1:8020/health` 检查 OCR 服务；主服务的配置状态不代表 OCR 已成功加载。网页需框选万用表显示屏后请求 OCR。对于屏幕不显示单位的表，用户可在界面选择直流电压的 200m、2、20 档，并在每次识别前现场核对确认；系统按确认档位解释无单位数字，记录档位来源，不会推断缺失的小数点。无确认时仍要求识别文字包含 V 或 mV。无法识别时请手动输入。候选结果必须由人核对，不用于证明接线安全。

威斯利 DT-9205A+ 实拍图片的初步测试中，通用 PP-OCRv4 未能正确识别七段数字；灰度、对比度和二值化的简单预处理也未解决。不要将该结果当成实际测量，后续需采集多张不同读数和拍摄条件的图片改进并验证。

首次安装依赖需 Python 3.11、CIX NOE_Engine 和 libnoe，以及 numpy、opencv-python-headless、shapely、pyclipper、Pillow、FastAPI、uvicorn 和 python-multipart。
