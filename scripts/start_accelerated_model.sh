#!/usr/bin/env bash
# Tested O6 runtime; backend is selected explicitly by the user service override.
set -eu

task_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
task_engine="${LABAGENT_ACCEL_LLAMA_SERVER:-/home/radxa/llama.cpp/build-codex-vulkan/bin/llama-server}"
task_backend="${LABAGENT_ACCEL_BACKEND:-cpu}"
task_cpus="${LABAGENT_INFERENCE_CPUS:-0,1,6,7,8,9,10,11}"
task_kind="${1:-}"
export VK_ICD_FILENAMES="${VK_ICD_FILENAMES:-/etc/vulkan/icd.d/mali.json}"

if [ ! -x "$task_engine" ]; then
  printf '%s\n' "Accelerated runtime unavailable: $task_engine" >&2
  exit 1
fi

case "$task_kind" in
  llm)
    task_model="$task_root/models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf"
    task_args=(--model "$task_model" --alias qwen3-4b-local
      --host 127.0.0.1 --port "${LABAGENT_LLM_PORT:-8081}"
      --ctx-size 8192 --threads 8 --threads-batch 8 --parallel 1
      --n-predict 1536 --threads-http 2 --metrics --jinja
      --chat-template-kwargs '{"enable_thinking":false}'
      --temp 0.7 --top-p 0.8 --top-k 20 --min-p 0)
    ;;
  vlm)
    task_model="$task_root/models/qwen3-vl-4b-gguf/Qwen3VL-4B-Instruct-Q4_K_M.gguf"
    task_args=(--model "$task_model"
      --mmproj "$task_root/models/qwen3-vl-4b-gguf/mmproj-Qwen3VL-4B-Instruct-F16.gguf"
      --alias qwen3-vl-4b-local --host 127.0.0.1 --port "${LABAGENT_VLM_PORT:-8032}"
      --ctx-size 4096 --threads 8 --threads-batch 8 --parallel 1
      --jinja --temp 0 --n-predict 512)
    ;;
  *)
    printf '%s\n' 'Usage: start_accelerated_model.sh llm|vlm' >&2
    exit 2
    ;;
esac

if [ ! -f "$task_model" ]; then
  printf '%s\n' "Model unavailable: $task_model" >&2
  exit 1
fi

case "$task_backend" in
  cpu)
    task_args+=(--device none --n-gpu-layers 0 --no-kv-offload --no-op-offload)
    if [ "$task_kind" = vlm ]; then task_args+=(--no-mmproj-offload); fi
    ;;
  vulkan)
    task_args+=(--device Vulkan0 --n-gpu-layers 99)
    ;;
  vulkan_vision)
    if [ "$task_kind" != vlm ]; then
      printf '%s\n' 'vulkan_vision requires the vlm model.' >&2
      exit 2
    fi
    export MTMD_BACKEND_DEVICE=Vulkan0
    task_args+=(--device none --n-gpu-layers 0 --no-kv-offload --no-op-offload)
    ;;
  *)
    printf '%s\n' "Unsupported acceleration backend: $task_backend" >&2
    exit 2
    ;;
esac

exec taskset -c "$task_cpus" "$task_engine" "${task_args[@]}"
