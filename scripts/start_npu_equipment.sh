#!/usr/bin/env bash
# Equipment vision on CIX NPU; language generation on CPU. Other VLM tasks use 8032.
set -eu
task_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
task_runtime="${LABAGENT_NPU_LLAMA_SERVER:-/home/radxa/llama.cpp-codex-npu/build/bin/llama-server}"
task_run_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export LABAGENT_NPU_VISION_SOCKET="$task_run_dir/labagent-npu-vision.sock"
test -x "$task_runtime"
exec taskset -c 0,1,6,7,8,9,10,11 "$task_runtime" \
  --model "$task_root/models/qwen3-vl-4b-gguf/Qwen3VL-4B-Instruct-Q4_K_M.gguf" \
  --mmproj "$task_root/models/qwen3-vl-4b-gguf/mmproj-Qwen3VL-4B-Instruct-F16.gguf" \
  --alias qwen3-vl-4b-local --host 127.0.0.1 --port 8034 \
  --ctx-size 4096 --threads 8 --threads-batch 8 --parallel 1 \
  --jinja --temp 0 --n-predict 512 --device none --n-gpu-layers 0 \
  --no-kv-offload --no-op-offload --no-mmproj-offload \
  --image-min-tokens 100 --image-max-tokens 100
