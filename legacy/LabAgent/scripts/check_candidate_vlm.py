"""Compare a CIX-listed MNN VLM on a saved image and negative controls."""
import argparse
import json
import tempfile
import time
from pathlib import Path

import MNN.cv as cv
import MNN.llm as llm
from PIL import Image, ImageOps


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    image = ImageOps.exif_transpose(Image.open(args.image)).convert("RGB")
    image.thumbnail((768, 768))
    blank = Image.new("RGB", image.size, "white")
    prompt = ('List only clearly visible objects from: digital multimeter, battery, red test probe, '
              'black test probe. Answer in JSON with keys "multimeter", "battery", "red_probe", '
              '"black_probe" and boolean values. Do not guess.')
    bounding = ('Locate the digital multimeter, battery, red test probe and black test probe. '
                'Return a JSON list of {"label": "...", "bbox_2d": [x1,y1,x2,y2]} '
                'in pixel coordinates. Omit anything not clearly visible.')
    model = llm.create(args.model)
    model.set_config({"max_new_tokens": 256, "reuse_kv": False,
                      "use_template": False, "sampler_type": "greedy"})
    model.load()
    print("MODEL_LOADED", flush=True)
    with tempfile.TemporaryDirectory() as directory:
        for name, frame, question in (("scene_presence", image, prompt),
                                      ("blank_presence", blank, prompt),
                                      ("scene_boxes", image, bounding),
                                      ("blank_boxes", blank, bounding)):
            path = Path(directory) / (name + ".png")
            frame.save(path)
            var = cv.imread(str(path))
            text = ("<|im_start|>user\n<img>image_0</img>" + question +
                    "<|im_end|>\n<|im_start|>assistant\n")
            model.reset()
            started = time.monotonic()
            result = model.response({"text": text, "images": [{"data": var,
                                     "height": frame.height, "width": frame.width}]}, False)
            print(json.dumps({"case": name, "seconds": round(time.monotonic() - started, 2),
                              "status": str(model.context.status), "result": result}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
