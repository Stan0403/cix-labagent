#!/usr/bin/env bash
set -eu
task_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if ! systemctl --user start labagent-lcd-capture.service; then
  if command -v zenity >/dev/null; then zenity --error --text='液晶采集服务未启动，请检查 labagent-lcd-capture 服务日志。'; fi
  exit 1
fi
"$task_root/.venv-app/bin/python" - <<'PY'
import json,time,urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(30):
    try:
        with opener.open('http://127.0.0.1:8766/health',timeout=1) as r:
            if json.load(r).get('service')=='lcd-keyboard-capture':break
    except OSError:pass
    time.sleep(.2)
else:raise SystemExit('液晶采集服务暂时无法连接，请查看服务日志')
PY
if command -v firefox >/dev/null; then
  exec firefox --new-tab http://127.0.0.1:8766/
fi
exec xdg-open http://127.0.0.1:8766/
