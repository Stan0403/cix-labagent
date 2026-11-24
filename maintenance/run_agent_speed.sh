#!/usr/bin/env bash
set -eu
task_python=/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo/.venv-app/bin/python
task_work=/tmp/codex-agent-speed
"$task_python" -u /tmp/benchmark_agent_decisions.py --source "$task_work/baseline" --output "$task_work/before" --variant baseline --resume
cd "$task_work/candidate"
"$task_python" -m pytest -q --junitxml="$task_work/board-tests.xml"
"$task_python" -u /tmp/benchmark_agent_decisions.py --source "$task_work/candidate" --output "$task_work/after" --variant optimized
"$task_python" -u /tmp/benchmark_agent_decisions.py --source "$task_work/baseline" --output "$task_work/cold-before" --variant baseline-cold --cases ready_to_measure --reset-prefix
"$task_python" -u /tmp/benchmark_agent_decisions.py --source "$task_work/candidate" --output "$task_work/cold-after" --variant optimized-cold --cases ready_to_measure --reset-prefix
"$task_python" -u /tmp/benchmark_agent_decisions.py --source "$task_work/candidate" --output "$task_work/native-after" --variant optimized-tools --format tools --cases equipment_unobserved ready_to_measure
printf 'VALIDATION_FINISHED\n'
