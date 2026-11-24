#!/usr/bin/env bash
set -eu
task_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$task_root"
exec .venv-ocr/bin/python -m uvicorn scripts.cix_ocr_service:app --host 127.0.0.1 --port 8020 --workers 1
