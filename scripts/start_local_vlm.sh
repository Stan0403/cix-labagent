#!/usr/bin/env bash
set -eu
task_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$task_root"
exec .venv-vlm/bin/python -m uvicorn scripts.mnn_vlm_service:app --host 127.0.0.1 --port 8030 --workers 1
