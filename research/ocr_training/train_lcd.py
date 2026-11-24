"""LCD domain fine-tuning of CIX's actual recognizer; no production writes.

The BiLSTM stays frozen. We train the CTC classifier and final ResNet stage,
then replace tensor constants in the original ONNX graph, preserving its NPU
operator topology. An exported ONNX is not a compiled or validated NPU binary.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import time
import cv2
import numpy as np

os.environ.setdefault(
    "TORCHINDUCTOR_CACHE_DIR", str(Path(__file__).resolve().parent / "cache/inductor")
)
os.environ.setdefault(
    "TORCH_HOME", str(Path(__file__).resolve().parent / "cache/torch")
)
import torch
from torch import nn
from cix_rec_model import ROOT, CixRecognizer, preprocess, characters, decode


def load_rows(name):
    return [
        json.loads(line)
        for line in (ROOT / "data" / name).read_text(encoding="utf-8").splitlines()
        if line
    ]


def tensor(row, augment=False, rng=None, geometry=False):
    image = cv2.imread(str(ROOT / "data" / row["path"]))
    if image is None:
        raise ValueError(row["path"])
    if augment:
        # Mild brightness / camera noise only; all sign/decimal pixels stay in frame.
        image = np.uint8(
            np.clip(
                image.astype(np.float32) * rng.uniform(0.85, 1.15) + rng.uniform(-8, 8),
                0,
                255,
            )
        )
        if geometry:
            h, w = image.shape[:2]
            angle = rng.uniform(-8, 8)
            rad = abs(angle) * np.pi / 180
            fit = min(
                w / (w * np.cos(rad) + h * np.sin(rad)),
                h / (h * np.cos(rad) + w * np.sin(rad)),
            )
            matrix = cv2.getRotationMatrix2D(
                (w / 2, h / 2), angle, fit * rng.uniform(0.92, 1)
            )
            image = cv2.warpAffine(
                image, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE
            )
    return preprocess(image)


@torch.no_grad()
def evaluate(model, rows, batch=8, details=False):
    model.eval()
    matches = 0
    sign = 0
    point = 0
    blank_ok = 0
    blank_count = 0
    records = []
    start = time.perf_counter()
    for offset in range(0, len(rows), batch):
        chunk = rows[offset : offset + batch]
        x = torch.from_numpy(np.stack([tensor(row) for row in chunk])).cuda()
        result = decode(model(x).cpu().numpy())
        for row, pred in zip(chunk, result):
            gt = row["label"]
            matches += pred == gt
            sign += pred.startswith("-") == gt.startswith("-")
            # Character-index equality catches shifted/missing/extra decimal points.
            point += [i for i, c in enumerate(pred) if c == "."] == [
                i for i, c in enumerate(gt) if c == "."
            ]
            if gt == "":
                blank_count += 1
                blank_ok += pred == ""
            if details:
                records.append(
                    {
                        "path": row["path"],
                        "truth": gt,
                        "prediction": pred,
                        "correct": pred == gt,
                    }
                )
    report = {
        "count": len(rows),
        "exact_correct": matches,
        "exact_accuracy": matches / max(1, len(rows)),
        "sign_accuracy": sign / max(1, len(rows)),
        "decimal_position_accuracy": point / max(1, len(rows)),
        "blank_count": blank_count,
        "blank_correct": blank_ok,
        "seconds": time.perf_counter() - start,
    }
    if details:
        report["records"] = records
    return report


def export_check(model, path, rows):
    import onnxruntime as ort

    model.eval()
    model.write_onnx(path)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    session = ort.InferenceSession(
        str(path), options, providers=["CPUExecutionProvider"]
    )
    checks = []
    for row in rows:
        x = tensor(row)[None]
        actual = session.run(None, {"x": x})[0]
        with torch.no_grad():
            expected = model(torch.from_numpy(x).cuda()).softmax(-1).cpu().numpy()
        error = float(np.max(np.abs(expected - actual)))
        if error > 2e-4 or decode(actual) != decode(expected):
            raise AssertionError({"export_error": error, "path": row["path"]})
        checks.append(
            {
                "path": row["path"],
                "max_abs_error": error,
                "prediction": decode(actual)[0],
            }
        )
    return {
        "passed": True,
        "checks": checks,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=1000)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--output", default="runs/lcd-v1")
    parser.add_argument("--resume", default="")
    parser.add_argument("--init-checkpoint", default="")
    parser.add_argument("--head-lr", type=float, default=5e-4)
    parser.add_argument("--backbone-lr", type=float, default=2e-5)
    parser.add_argument("--real-manifest", default="real.jsonl")
    parser.add_argument("--geometry", action="store_true")
    args = parser.parse_args()
    if args.resume and args.init_checkpoint:
        parser.error("--resume and --init-checkpoint are mutually exclusive")
    out = ROOT / args.output
    out.mkdir(parents=True, exist_ok=True)
    if (out / "latest.pt").exists() and not args.resume:
        raise FileExistsError(
            "Run already has a checkpoint; use --resume or choose a new --output"
        )
    if not json.loads((ROOT / "parity.json").read_text(encoding="utf-8")).get("passed"):
        raise RuntimeError("Run original-model parity check before training")
    seed = 20261122
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.set_num_threads(4)
    torch.backends.cudnn.benchmark = False
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    rng = random.Random(seed)
    synthetic = load_rows("synthetic.jsonl")
    real = load_rows(args.real_manifest)
    synth_train = [r for r in synthetic if r["split"] == "train"]
    train_real = [r for r in real if r["split"] == "train"]
    validation = [r for r in synthetic if r["split"] == "validation"]
    holdout = [r for r in real if r["split"] == "holdout"]
    train_hashes = {r["sha256"] for r in synth_train + train_real}
    if train_hashes & {r["sha256"] for r in validation + holdout}:
        raise ValueError("Train/eval image leakage")
    real_train_groups = {r.get("group") for r in train_real if r.get("group")}
    if real_train_groups & {r.get("group") for r in holdout if r.get("group")}:
        raise ValueError("A camera capture group occurs in both train and holdout")
    if {r.get("source_sha256") for r in train_real if r.get("source_sha256")} & {
        r.get("source_sha256") for r in holdout if r.get("source_sha256")
    }:
        raise ValueError("A source frame occurs in both train and holdout")
    model = CixRecognizer().cuda()
    if args.init_checkpoint:
        model.load_state_dict(
            torch.load(args.init_checkpoint, map_location="cuda", weights_only=False)[
                "model"
            ]
        )
    for param in model.parameters():
        param.requires_grad_(False)
    for param in model.head.parameters():
        param.requires_grad_(True)
    for param in model.stages[-1].parameters():
        param.requires_grad_(True)
    optimizer = torch.optim.AdamW(
        [
            {"params": model.head.parameters(), "lr": args.head_lr},
            {"params": model.stages[-1].parameters(), "lr": args.backbone_lr},
        ],
        weight_decay=1e-5,
    )
    loss_fn = nn.CTCLoss(blank=0, zero_infinity=False)
    vocab = {char: i for i, char in enumerate(characters())}
    configuration = {
        "steps": args.steps,
        "batch": args.batch,
        "seed": seed,
        "gpu": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "training": "CTC head + final CNN stage; frozen BiLSTM and BN statistics",
        "synthetic_train": len(synth_train),
        "synthetic_validation": len(validation),
        "camera_train": len(train_real),
        "camera_diagnostic_holdout": len(holdout),
        "camera_mix_probability": 0.35,
        "full_dictionary_classes": len(vocab),
        "real_manifest": args.real_manifest,
        "geometry_augmentation": args.geometry,
        "resume_source": args.resume,
        "init_checkpoint": args.init_checkpoint,
        "head_learning_rate": args.head_lr,
        "backbone_learning_rate": args.backbone_lr,
        "model_sha256": hashlib.sha256(
            (ROOT / "assets/rec.onnx").read_bytes()
        ).hexdigest(),
        "production_modified": False,
        "NPU_compiled": False,
    }
    (out / "config.json").write_text(
        json.dumps(configuration, indent=2), encoding="utf-8"
    )
    baseline = {}
    history = []
    best = -1
    start_step = 0
    if args.resume:
        saved = torch.load(args.resume, map_location="cuda", weights_only=False)
        model.load_state_dict(saved["model"])
        optimizer.load_state_dict(saved["optimizer"])
        start_step = saved["step"]
        best = saved["best"]
        history = saved["history"]
        baseline = saved["baseline"]
        if "rng_state" in saved:
            rng.setstate(saved["rng_state"])
            torch.set_rng_state(saved["torch_rng_state"].cpu())
            torch.cuda.set_rng_state_all(
                [state.cpu() for state in saved["cuda_rng_states"]]
            )
        else:
            print(
                json.dumps(
                    {
                        "resume_warning": "Older checkpoint has no sampler RNG state; continuation is not bit-identical"
                    }
                ),
                flush=True,
            )
        previous_best = Path(args.resume).parent / "best.pt"
        if previous_best.resolve() != (out / "best.pt").resolve():
            import shutil

            shutil.copy2(previous_best, out / "best.pt")
    else:
        baseline = {
            "synthetic_validation": evaluate(model, validation, details=True),
            "camera_diagnostic_holdout": evaluate(model, holdout, details=True),
            "camera_training_fit_only": evaluate(model, train_real, details=True),
        }
        (out / "baseline.json").write_text(
            json.dumps(baseline, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "baseline_synthetic": baseline["synthetic_validation"][
                        "exact_accuracy"
                    ],
                    "baseline_camera": baseline["camera_diagnostic_holdout"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    started = time.perf_counter()
    for step in range(start_step + 1, args.steps + 1):
        model.eval()
        model.rnn.train()  # cuDNN backward; all BN running statistics remain frozen.
        chunk = [
            rng.choice(train_real if rng.random() < 0.35 else synth_train)
            for _ in range(args.batch)
        ]
        images = torch.from_numpy(
            np.stack([tensor(row, True, rng, args.geometry) for row in chunk])
        ).cuda()
        labels = [
            torch.tensor([vocab[c] for c in row["label"]], dtype=torch.long)
            for row in chunk
        ]
        targets = torch.cat(labels).cuda()
        lengths = torch.tensor([len(label) for label in labels], dtype=torch.long)
        optimizer.zero_grad(set_to_none=True)
        logits = model(images)
        loss = loss_fn(
            logits.log_softmax(-1).transpose(0, 1),
            targets,
            torch.full((args.batch,), logits.shape[1], dtype=torch.long),
            lengths,
        )
        if not torch.isfinite(loss):
            raise FloatingPointError(f"Nonfinite CTC loss at {step}")
        loss.backward()
        nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 5)
        optimizer.step()
        if step % 50 == 0:
            log = {
                "step": step,
                "loss": float(loss.detach()),
                "elapsed_seconds": time.perf_counter() - started,
                "allocated_gpu_mib": torch.cuda.max_memory_allocated() / 2**20,
            }
            print(json.dumps(log), flush=True)
        if step % 200 == 0 or step == args.steps:
            metric = evaluate(model, validation)
            history.append(
                {
                    "step": step,
                    "loss": float(loss.detach()),
                    "synthetic_validation": metric,
                }
            )
            if metric["exact_accuracy"] > best:
                best = metric["exact_accuracy"]
                torch.save(
                    {
                        "model": model.state_dict(),
                        "step": step,
                        "synthetic_accuracy": best,
                    },
                    out / "best.pt",
                )
            checkpoint = {
                "model": model.state_dict(),
                "optimizer": optimizer.state_dict(),
                "step": step,
                "best": best,
                "history": history,
                "baseline": baseline,
                "rng_state": rng.getstate(),
                "torch_rng_state": torch.get_rng_state(),
                "cuda_rng_states": torch.cuda.get_rng_state_all(),
            }
            torch.save(checkpoint, out / "latest.pt")
            (out / "history.json").write_text(
                json.dumps(history, indent=2), encoding="utf-8"
            )
            print(
                json.dumps({"validation_step": step, "metric": metric, "best": best}),
                flush=True,
            )
    best_model = torch.load(out / "best.pt", map_location="cuda", weights_only=False)
    model.load_state_dict(best_model["model"])
    final = {
        "configuration": configuration,
        "selected_step": best_model["step"],
        "model_selection": "synthetic validation exact accuracy only; camera holdout never used for selection",
        "baseline": baseline,
        "trained": {
            "synthetic_validation": evaluate(model, validation, details=True),
            "camera_diagnostic_holdout": evaluate(model, holdout, details=True),
            "camera_training_fit_only": evaluate(model, train_real, details=True),
        },
        "training_seconds": time.perf_counter() - started,
        "limits": [
            f"{len(train_real)} real training crops from a narrow range of scenes/readings",
            "Only 4 camera holdout crops; same meter/camera; session independence unverified",
            "Camera diagnostic images were inspected during development; not an untouched final test set",
            "Synthetic validation measures rendered images, not real-world accuracy",
            "Domain-specific LCD adaptation may reduce general text OCR capability",
            "ONNX export does not establish quantized NPU accuracy or production readiness",
        ],
    }
    final["export"] = export_check(model, out / "rec-lcd.onnx", holdout)
    (out / "report.json").write_text(
        json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "finished": True,
                "output": str(out),
                "selected_step": final["selected_step"],
                "synthetic_accuracy": final["trained"]["synthetic_validation"][
                    "exact_accuracy"
                ],
                "camera": final["trained"]["camera_diagnostic_holdout"],
                "export": final["export"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
