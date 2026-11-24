"""Validate actual CIX NPU predictions against the held-out real captures."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from labagent.yolo_equipment import CLASSES, EquipmentDetector


def iou(a, b):
    a=np.array([a[0],a[1],a[0]+a[2],a[1]+a[3]])
    b=np.array([b[0],b[1],b[0]+b[2],b[1]+b[3]])
    overlap=np.maximum(np.minimum(a[2:],b[2:])-np.maximum(a[:2],b[:2]),0).prod()
    return float(overlap/max((a[2:]-a[:2]).prod()+(b[2:]-b[:2]).prod()-overlap,1e-9))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--raw",type=Path,required=True)
    parser.add_argument("--dataset",type=Path,required=True)
    parser.add_argument("--model",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    frames=[r for r in json.loads((args.dataset/"manifest.json").read_text()) if r["split"]=="val"]
    if len(frames)!=25 or any(r["kind"]!="real" for r in frames):
        raise ValueError("Expected the original 25 held-out real captures")
    detector=EquipmentDetector(args.model)
    counts={name:dict(tp=0,fp=0,fn=0) for name in CLASSES}
    details=[]
    try:
        for _ in range(3):
            detector.detect(Image.open(args.raw/frames[0]["filename"]))
        for row in frames:
            image=args.raw/row["filename"]
            if hashlib.sha256(image.read_bytes()).hexdigest()!=row["sha256"]:
                raise ValueError(f"Validation image hash mismatch: {image.name}")
            objects,timing=detector.detect(Image.open(image))
            predictions=[d for d in objects if d["confidence"]>=.35]
            truth={name:[] for name in CLASSES}
            for line in (args.dataset/"labels/val"/image.with_suffix(".txt").name).read_text().splitlines():
                c,x,y,w,h=map(float,line.split())
                truth[CLASSES[int(c)]].append([x-w/2,y-h/2,w,h])
            per_image={}
            for name in CLASSES:
                remaining=set(range(len(truth[name])))
                entry=dict(tp=0,fp=0,fn=0)
                for prediction in sorted((p for p in predictions if p["label"]==name),key=lambda p:p["confidence"],reverse=True):
                    match=max(remaining,key=lambda k:iou(prediction["bbox"],truth[name][k])) if remaining else None
                    if match is not None and iou(prediction["bbox"],truth[name][match])>=.5:
                        remaining.remove(match)
                        entry["tp"]+=1
                    else:
                        entry["fp"]+=1
                entry["fn"]=len(remaining)
                per_image[name]=entry
                for field,value in entry.items():
                    counts[name][field]+=value
            details.append(dict(image=image.name,counts=per_image,predictions=predictions,**timing))
            print(image.name, per_image, flush=True)
    finally:
        detector.close()
    total={field:sum(x[field] for x in counts.values()) for field in ("tp","fp","fn")}
    total["precision"]=total["tp"]/max(total["tp"]+total["fp"],1)
    total["recall"]=total["tp"]/max(total["tp"]+total["fn"],1)
    report=dict(backend="yolo26n-cix-npu",model_sha256=hashlib.sha256(args.model.read_bytes()).hexdigest(),
                confidence=.35,matching_iou=.5,nms_iou=.45,summary=counts,total=total,
                latency={key:{"mean_ms":float(np.mean([x[key] for x in details])),
                              "p95_ms":float(np.percentile([x[key] for x in details],95))}
                         for key in ("npu_ms","inference_ms","total_ms")},images=details,
                limitation="Same desk and camera; no independent generalization claim. Image decoding and HTTP excluded.")
    args.output.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!="images"}),flush=True)


if __name__=="__main__":
    main()
