# LabAgent YOLO26n v1

## Current deployment: CIX NPU (2026-11-19)

Production: `equipment_yolo26n_960_heads_int8_bias32_packed.cix`.
SHA256: `a4dd692a0eb944d2379ca98f3539f3a8a770560ddf91940b570c22cb0a01760d`.
The live equipment route now uses YOLO26n on the Orion O6 NPU.
Other VLM tools, dialogue and NPU speech remain available.

Actual NPU validation: 25 real held-out images, confidence 0.35,
NMS IoU 0.45, matching IoU 0.5. TP 70, FP 0, FN 6;
precision 1.0, recall 0.9211. All six misses were batteries.
Meters: 25/25; batteries: 21/27; red and black probes: each 12/12.
These are same-camera baseline results, not independent generalization evidence.

After 3 warmups, average NPU synchronous execution was 25.85 ms;
including quantization/output retrieval/head decoding, 35.20 ms;
including resize/letterbox/NMS, 69.37 ms. File decode, capture and HTTP excluded.
Two real adapter HTTP smoke tests detected all four classes; the web-style
compressed upload took 90.7 ms. Reports: `npu-validation.json`, `http-smoke.json`.

Input is RGB NCHW [1,3,960,960], letterbox 114, float32 /255;
NOE quantizes to uint8. Two INT8 outputs [1,4,18900] contain regression
distances and class logits. CPU performs grid decoding, sigmoid and NMS.
Weights/activations use INT8; bias uses 32 bits. Calibration used 40 training images.
`*.aipu.bin` files are ELF compiler intermediates, **not** NOE-loadable containers.
Only the cixpack-wrapped `*_packed.cix` is deployed.

Integration, settings and rollback: `../../docs/yolo26-npu-deployment.md`.

## Earlier FP32 CPU baseline (comparison only)

Trained 2026-11-19 for 150 epochs from official Ultralytics YOLO26n weights.
Dataset: 95 real + 3 synthetic training frames; 25 real validation frames.
Four classes: multimeter, battery, red_probe, black_probe (see classes.json).

FP32 comparison model: equipment_yolo26n_960.onnx.
Alternative lower-latency model: equipment_yolo26n_640.onnx.
Training checkpoint: runs/equipment_yolo26n_v1/weights/best.pt.

| Input | Validation mAP50 | Validation mAP50-95 | O6 CPU mean inference |
| --- | --- | --- | --- |
| 640 | 0.91696 | 0.63352 | 58.10 ms |
| 960 | 0.98945 | 0.67584 | 133.95 ms |

O6 benchmark: ONNX Runtime 1.31.1 CPUExecutionProvider, 4 CPU threads,
3 warmups + 15 timed runs on capture 20261119_batch04_003.jpg.
Timing excludes frame capture, JPEG decode, preprocessing and NMS.
These older timings describe CPU execution only. Actual NPU results are above.

Export: ONNX opset 13, float32 NCHW, static batch 1, raw one-to-many output
(1, 8, 8400) for 640, (1, 8, 18900) for 960. Confidence filtering and NMS
are required. Class order is listed in classes.json.

The earlier CPU benchmark used an isolated directory. The following values apply
only to the older ONNX adapter; the live plan-driven app uses the systemd YOLO
equipment service described in the deployment document:

```dotenv
LABAGENT_VISION_PROVIDER=onnx
LABAGENT_ONNX_MODEL=models/equipment-yolo26-v1/equipment_yolo26n_960.onnx
LABAGENT_ONNX_LABELS=models/equipment-yolo26-v1/classes.json
LABAGENT_ONNX_SIZE=960
LABAGENT_ONNX_PROVIDERS=CPUExecutionProvider
```

Check the active experiment's supported labels include all four classes.
Review the confidence threshold against the validation samples: the existing
0.85 workflow threshold may reject otherwise correct probe predictions.
All images come from the same desk/camera. Validation scores are a first baseline,
not independent evidence of reliability on new environments.

Server training directory: /root/autodl-tmp/labagent-yolo26
Full training parameters, environment versions and validation images are included.
