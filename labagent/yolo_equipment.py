"""Four-class equipment detection shared by CPU baselines and CIX NPU serving."""
from pathlib import Path
import re
import threading
import time

import numpy as np
from PIL import Image

CLASSES = ("multimeter", "battery", "red_probe", "black_probe")


def requested_classes(key, name):
    text = (key + " " + name).lower().replace("-", "_")
    if any(x in name for x in ("电池盒", "电池夹", "电池座", "充电器")) or "holder" in text:
        return ()
    # Names come from reviewed experiment plans, not instructions to the detector.
    if "表笔" in name or "probe" in text or "test lead" in text or "test_lead" in text:
        red = "红" in name or "red" in text
        black = "黑" in name or "black" in text
        if red and not black:
            return ("red_probe",)
        if black and not red:
            return ("black_probe",)
        return ("red_probe", "black_probe")
    if "万用表" in name or "multimeter" in text:
        return ("multimeter",)
    if ("电池" in name and not any(x in name for x in ("电池盒", "电池夹", "电池座"))) or re.search(r"\bbatter(?:y|ies)\b", text):
        return ("battery",)
    return ()


def prepare(image, size):
    image = image.convert("RGB")
    ratio = min(size / image.width, size / image.height)
    w, h = round(image.width * ratio), round(image.height * ratio)
    left, top = (size-w)//2, (size-h)//2
    resized = image.resize((w,h), Image.Resampling.BILINEAR)
    padded = Image.new("RGB", (size,size), (114,114,114))
    padded.paste(resized, (left,top))
    tensor = np.asarray(padded, dtype=np.float32).transpose(2,0,1)[None] / 255.
    return np.ascontiguousarray(tensor), (left,top,w,h)


def decode_raw_heads(box_distances, class_logits, size):
    """Decode YOLO26 reg_max=1 heads outside the quantized graph."""
    boxes = np.asarray(box_distances,dtype=np.float32).reshape(1,4,-1)
    logits = np.asarray(class_logits,dtype=np.float32).reshape(1,4,-1)
    anchors, strides = [], []
    for stride in (8,16,32):
        grid = size//stride
        y,x = np.meshgrid(np.arange(grid),np.arange(grid),indexing="ij")
        anchors.append(np.stack((x.ravel()+.5,y.ravel()+.5)))
        strides.append(np.full(grid*grid,stride))
    anchors = np.concatenate(anchors,axis=1)[None]
    strides = np.concatenate(strides)[None,None]
    if boxes.shape[-1] != anchors.shape[-1] or logits.shape != boxes.shape:
        raise ValueError("Compiled raw head sizes do not match YOLO26 strides")
    lo,hi = anchors-boxes[:,:2], anchors+boxes[:,2:]
    xywh = np.concatenate(((lo+hi)/2,hi-lo),axis=1)*strides
    scores = 1/(1+np.exp(-np.clip(logits,-80,80)))
    return np.concatenate((xywh,scores),axis=1)


def decode(output, geometry, threshold=.15, iou_threshold=.45):
    output = np.asarray(output, dtype=np.float32)
    if output.ndim == 1:
        output = output.reshape(1,8,-1)
    if output.ndim != 3 or output.shape[0] != 1 or output.shape[1] != 8:
        raise ValueError(f"Expected raw YOLO output (1,8,N), got {output.shape}")
    if not np.isfinite(output).all():
        raise ValueError("Non-finite detector output")
    rows = output[0].T
    ids = rows[:,4:].argmax(axis=1)
    scores = rows[np.arange(len(rows)), ids+4]
    chosen = np.flatnonzero(scores >= threshold)
    chosen = chosen[np.argsort(scores[chosen])[::-1]][:1000]
    centers, wh = rows[:,:2], rows[:,2:4]
    xyxy = np.column_stack((centers-wh/2, centers+wh/2))
    keep = []
    while len(chosen):
        i = chosen[0]
        keep.append(i)
        rest = chosen[1:]
        if not len(rest) or len(keep) >= 50:
            break
        lo = np.maximum(xyxy[i,:2], xyxy[rest,:2])
        hi = np.minimum(xyxy[i,2:], xyxy[rest,2:])
        area = np.maximum(hi-lo,0).prod(axis=1)
        a = np.maximum(xyxy[i,2:]-xyxy[i,:2],0).prod()
        b = np.maximum(xyxy[rest,2:]-xyxy[rest,:2],0).prod(axis=1)
        overlap = area / np.maximum(a+b-area,1e-9)
        chosen = rest[(ids[rest] != ids[i]) | (overlap <= iou_threshold)]
    left, top, w, h = geometry
    result = []
    for i in keep:
        x1,y1,x2,y2 = xyxy[i]
        x1,x2 = np.clip((np.array([x1,x2])-left)/w,0,1)
        y1,y2 = np.clip((np.array([y1,y2])-top)/h,0,1)
        if x2 > x1 and y2 > y1:
            result.append({"label":CLASSES[ids[i]], "confidence":float(scores[i]),
                           "bbox":[float(x1),float(y1),float(x2-x1),float(y2-y1)]})
    return result


def inspect_items(detections, items, confidence=.35):
    present, boxes, unsupported = {}, [], []
    for key,name in items.items():
        classes = requested_classes(key,name)
        if not classes:
            present[key] = None
            unsupported.append(key)
            continue
        good = [d for d in detections if d["label"] in classes and d["confidence"] >= confidence]
        found = {d["label"] for d in good}
        if all(c in found for c in classes):
            present[key] = True
        elif any(d["label"] in classes for d in detections):
            present[key] = None
        else:
            present[key] = False
        boxes.extend({**d, "label":key, "model_class":d["label"]} for d in good)
    return {"present":present, "boxes":boxes, "coordinate_space":"normalized_xywh",
            "requires_confirmation":True, "unsupported":unsupported}


class EquipmentDetector:
    def __init__(self, model, size=960, backend="cix", confidence=.35):
        self.path = Path(model)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self.size, self.kind, self.confidence = size, backend, confidence
        self.lock = threading.Lock()
        self.requests = 0
        if backend == "cix":
            from NOE_Engine import EngineInfer
            from importlib import import_module
            self.engine = EngineInfer(str(self.path))
            self.output_tensor_kind = import_module(EngineInfer.__module__).NOE_TENSOR_TYPE_OUTPUT
            if len(self.engine.in_tensor_desc) != 1 or len(self.engine.out_tensor_desc) not in (1,2):
                self.engine.clean()
                raise ValueError("YOLO requires one input and either merged or two raw-head outputs")
        elif backend == "onnx":
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = 4
            options.inter_op_num_threads = 1
            self.engine = ort.InferenceSession(str(self.path), sess_options=options, providers=["CPUExecutionProvider"])
            if self.engine.get_inputs()[0].shape != [1,3,size,size]:
                raise ValueError("Model input size does not match configured detector size")
        else:
            raise ValueError("Explicit backend cix or onnx is required")

    @property
    def backend(self):
        return "yolo26n-cix-npu" if self.kind == "cix" else "yolo26n-onnx-cpu"

    def detect(self, image):
        start = time.perf_counter()
        tensor, geometry = prepare(image,self.size)
        with self.lock:
            before = time.perf_counter()
            if self.kind == "cix":
                # The CIX IR preserves the NCHW input and inserts its own
                # transpose before NHWC convolution kernels.
                inputs = tensor
                outputs = self._run_cix(inputs)
                output = (decode_raw_heads(*outputs,self.size) if len(outputs)==2
                          else np.asarray(outputs[0]).reshape(1,8,-1))
            else:
                output = self.engine.run(None,{self.engine.get_inputs()[0].name:tensor})[0]
            inference_ms = (time.perf_counter()-before)*1000
            npu_ms = self.last_npu_ms if self.kind == "cix" else None
            self.requests += 1
        objects = decode(output,geometry)
        return objects, {"inference_ms":inference_ms, "npu_ms":npu_ms,
                         "total_ms":(time.perf_counter()-start)*1000}

    def _run_cix(self, tensor):
        engine=self.engine
        engine._create_job()
        try:
            descriptor=engine.in_tensor_desc[0]
            dtype=engine.input_type[0]
            if np.issubdtype(dtype,np.integer):
                data=np.clip(np.round(tensor*descriptor.scale-descriptor.zero_point),
                             engine.input_dtype_min[0],engine.input_dtype_max[0]).astype(dtype)
            else:
                data=tensor.astype(dtype)
            if data.nbytes != descriptor.size:
                raise ValueError("NPU input byte size differs from compiled descriptor")
            status=engine.npu.noe_load_tensor(engine.job_id,0,data.tobytes())
            if status not in (None,0):
                raise RuntimeError(f"NPU input load failed: {status}")
            npu_start=time.perf_counter()
            status=engine.npu.noe_job_infer_sync(engine.job_id,-1)
            self.last_npu_ms=(time.perf_counter()-npu_start)*1000
            if status not in (None,0):
                raise RuntimeError(f"NPU inference failed: {status}")
            outputs=[]
            for i,descriptor in enumerate(engine.out_tensor_desc):
                status,raw=engine.npu.noe_get_tensor(engine.job_id,self.output_tensor_kind,i)
                if status != 0:
                    raise RuntimeError(f"NPU output retrieval failed: {status}")
                result=np.frombuffer(raw,dtype=engine.output_type[i])
                if np.issubdtype(result.dtype,np.integer):
                    result=(result.astype(np.float32)+descriptor.zero_point)/descriptor.scale
                outputs.append(result.astype(np.float32))
            return outputs
        finally:
            engine.npu.noe_clean_job(engine.job_id)

    def inspect(self,image,items):
        objects,timing = self.detect(image)
        return {**inspect_items(objects,items,self.confidence), **timing, "detections":objects,
                "backend":self.backend, "input_size":self.size, "raw_text":""}

    def close(self):
        if self.kind == "cix":
            self.engine.clean()
