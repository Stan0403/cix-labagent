"""Evaluate actual configured perception against human labels, not mock-generated scores."""
import argparse
import json
import time
from pathlib import Path

from labagent.config import Settings
from labagent.perception import Perception, load_image
from labagent.providers import Gateway
from labagent.rules import Engine


def main():
    parser = argparse.ArgumentParser(description="Evaluate frame-level object presence and voltage OCR")
    parser.add_argument("manifest", type=Path, help="JSON array, format documented in docs/TESTING.md")
    parser.add_argument("--allow-remote", action="store_true", help="Permit sending these images to configured VLM")
    parser.add_argument("--output", type=Path, default=Path("test-results/perception.json"))
    args = parser.parse_args()
    settings = Settings.load()
    if "vlm" in {settings.vision_provider, settings.ocr_provider} and not args.allow_remote:
        raise SystemExit("VLM sends image data to a configured service. Review the endpoint, then use --allow-remote.")
    engine = Engine(settings)
    gateway = Gateway(settings)
    perception = Perception(settings, gateway, engine.spec["required_objects"])
    rows = json.loads(args.manifest.read_text(encoding="utf-8"))
    results, tp, fp, fn, ocr_total, ocr_correct = [], 0, 0, 0, 0, 0
    try:
        for row in rows:
            image_path = args.manifest.parent / row["image"]
            image = load_image(image_path.read_bytes())
            start = time.perf_counter()
            result = {"image": row["image"]}
            if "objects" in row:
                output = perception.detect(image)
                actual = {x["label"] for x in output["objects"] if x["confidence"] >= settings.min_confidence}
                expected = set(row["objects"])
                tp += len(actual & expected)
                fp += len(actual - expected)
                fn += len(expected - actual)
                result["detection"] = output
                result["expected_objects"] = sorted(expected)
            if "voltage" in row:
                reading = perception.read_voltage(image, row["roi"])
                correct = (reading["value"] is not None and reading["confidence"] >= settings.min_confidence
                           and abs(reading["value"] - row["voltage"]) <= row.get("tolerance", 0.01))
                ocr_total += 1
                ocr_correct += int(correct)
                result.update(reading=reading, expected_voltage=row["voltage"], correct=correct)
            result["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
            results.append(result)
    finally:
        gateway.close()
    output = {"note": "Actual configured inference; presence metrics are NOT bounding-box mAP or electrical safety proof",
              "frames": len(results), "object_presence_precision": tp / (tp + fp) if tp + fp else None,
              "object_presence_recall": tp / (tp + fn) if tp + fn else None,
              "ocr_accuracy": ocr_correct / ocr_total if ocr_total else None, "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in output.items() if k != "results"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
