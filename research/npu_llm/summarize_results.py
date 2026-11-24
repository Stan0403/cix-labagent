"""Summarize measured results without implying a full-model conversion."""

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent
summary = {
    "full_model_migrated": False,
    "production_llm_backend": "llama.cpp CPU, port 8081",
    "production_application_changed": False,
    "model_sha256": "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
    "scope": "first transformer block only; offload blk.0.attn_q.weight; no generated text",
    "numerical_gate": "relative L2 < 0.005 per first-block output; experimental, not a quality acceptance standard",
    "block_probes": {},
}
for path in sorted(ROOT.glob("first-block*report.json")):
    report = json.loads(path.read_text())
    cases = report["cases"]
    entry = {"passed": report["passed"], "cases": len(cases)}
    if cases:
        for key in cases[0]:
            if key.endswith(("_seconds", "_relative_l2")):
                values = [case[key] for case in cases]
                entry[key] = {
                    "min": min(values),
                    "max": max(values),
                    "median": statistics.median(values),
                }
    summary["block_probes"][path.name] = entry
services = Path("/tmp/codex-qwen3-npu-existing-services.json")
if services.is_file():
    health = json.loads(services.read_text())
    summary["formal_service_checks"] = {
        str(port): "HTTP 200"
        for port in [8081, 8001, 8031, 8011]
        if str(port) in health
    }
    summary["actual_yolo_http_seconds"] = health["yolo_actual_inference"]["seconds"]
summary["remaining_work"] = [
    "LLM runtime integration for all 36 layers and the output head",
    "Variable prompt lengths and long KV cache correctness",
    "Whole-model generation, Agent tool JSON quality and independent evaluation",
    "Resolve persistent-context replay timeout and measure end-to-end gains",
    "INT8 calibration from original float weights and representative activation corpus",
]
(ROOT / "adaptation-summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2)
)
print(json.dumps(summary), flush=True)
