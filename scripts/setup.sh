#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,vision,serial]'
if [ ! -f .env ]; then cp .env.example .env; fi
printf '%s\n' 'Ready. Run: .venv/bin/python -m labagent'
