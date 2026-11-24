#!/usr/bin/env bash
# Run the selected CIX-catalog Qwen3 model using the board's existing llama.cpp runtime.
set -eu
task_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
task_engine="${LABAGENT_LLAMA_SERVER:-/usr/share/cix/bin/llama-server}"
task_model="$task_root/models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf"
if [ ! -x "$task_engine" ]; then
  printf '%s\n' "llama-server unavailable: $task_engine" >&2
  exit 1
fi
if [ ! -f "$task_model" ]; then
  printf '%s\n' "Model unavailable: $task_model; see docs/LOCAL_LLM.md" >&2
  exit 1
fi
cd "$task_root"
exec "$task_engine" \
  --model "$task_model" --alias qwen3-4b-local \
  --host 127.0.0.1 --port 8080 \
  --ctx-size 8192 --threads 8 --threads-batch 8 --parallel 1 \
  --n-predict 384 --threads-http 2 --metrics \
  --jinja --chat-template-kwargs '{"enable_thinking":false}' \
  --reasoning-budget 0 --reasoning-format deepseek \
  --temp 0.7 --top-p 0.8 --top-k 20 --min-p 0
