"""Small real-activation calibration/held-out inputs for the first Q projection."""

from pathlib import Path

import numpy as np
from probe_first_block import FirstBlock, rms, tokenize

root = Path(__file__).parent
model = FirstBlock()
calibration_prompts = [
    "实验助手需要检查电池、万用表和表笔。请确认直流电压20V档，再观察屏幕并读取电压。",
    "根据实验指导材料生成操作计划，要求保留现场人工确认和测量证据，最终保存两次稳定读数。",
    "两次电池电压读数为1.51V与1.50V，请判断当前实验的下一步行动，并简要解释原因。",
]
validation_prompt = "请将实验指导材料整理为操作计划：先断开电源，再确认万用表档位，连接表笔并读取电池电压，最后保存结果。不要代替操作者确认现场接线。"


def normalized(text):
    return rms(model.embed(tokenize(text)), model.w["attn_norm"], model.eps)


calibration = np.concatenate([normalized(p) for p in calibration_prompts])
validation = normalized(validation_prompt)[-25:]
np.save(root / "projection-calibration.npy", calibration)
np.save(root / "projection-validation24.npy", validation[:24])
np.save(root / "projection-validation1.npy", validation[24:])
print(
    {
        "calibration_shape": calibration.shape,
        "validation_shape": validation.shape,
        "calibration_abs_max": float(np.abs(calibration).max()),
        "validation_abs_max": float(np.abs(validation).max()),
    },
    flush=True,
)
