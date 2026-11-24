#!/usr/bin/env bash
set -eu
task_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
model_root="$task_root/models/qwen3-vl-4b-gguf"
exec /home/radxa/llama.cpp/build/bin/llama-server \
  --model "$model_root/Qwen3VL-4B-Instruct-Q4_K_M.gguf" \
  --mmproj "$model_root/mmproj-Qwen3VL-4B-Instruct-F16.gguf" \
  --alias qwen3-vl-4b-local --host 127.0.0.1 --port 8032 \
  --ctx-size 4096 --threads 8 --threads-batch 8 --parallel 1 \
  --n-gpu-layers 0 --no-mmproj-offload --jinja --temp 0 --n-predict 512
