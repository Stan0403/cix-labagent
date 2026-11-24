# CIX PP-OCRv4 本地 OCR

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
