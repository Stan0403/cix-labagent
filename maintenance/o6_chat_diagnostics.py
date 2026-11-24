"""Read-only dialogue latency and runtime inventory; never print credentials or message text."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from datetime import datetime

APP=Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
sys.path.insert(0,str(APP))
from labagent.config import Settings

settings=Settings.load()
report={'llm_model':settings.llm_model,'chat_provider':settings.chat_provider,'processes':[]}
allow={'--model','--alias','--port','--ctx-size','--threads','--threads-batch','--parallel','--n-gpu-layers','--chat-template-kwargs','--reasoning-budget','--reasoning-format'}
for proc in Path('/proc').iterdir():
    if not proc.name.isdecimal():continue
    try:
        args=(proc/'cmdline').read_bytes().decode().strip('\0').split('\0')
        if not args or Path(args[0]).name!='llama-server':continue
        filtered={args[i]:args[i+1] for i in range(len(args)-1) if args[i] in allow}
        if '--model' in filtered:filtered['--model']=Path(filtered['--model']).name
        fds=[]
        for path in (proc/'fd').iterdir():
            try:
                link=os.readlink(path)
                if link.startswith(('/dev/aipu','/dev/dri','/dev/mali')):fds.append(link)
            except OSError:pass
        report['processes'].append({'pid':int(proc.name),'executable':args[0],'flags':filtered,'accelerator_handles':sorted(set(fds))})
    except (OSError,UnicodeError):pass
report['loadavg']=Path('/proc/loadavg').read_text().split()[:3]
report['sources']={str(p.relative_to(APP)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [APP/'labagent/agent.py',APP/'labagent/providers.py',APP/'labagent/web/demo.js']}
database=settings.data_dir/'labagent.sqlite3'
with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True) as connection:
    rows=connection.execute('SELECT session_id,role,content,at FROM messages ORDER BY sequence DESC LIMIT 16').fetchall()[::-1]
    pending={};pairs=[]
    for sid,role,content,at in rows:
        if role=='user':pending[sid]=(at,len(content))
        elif role=='assistant' and sid in pending:
            began,chars=pending.pop(sid)
            pairs.append({'seconds':round((datetime.fromisoformat(at)-datetime.fromisoformat(began)).total_seconds(),3),
                          'question_chars':chars,'answer_chars':len(content),'at':at})
    report['recent_chat_pairs']=pairs
    states=connection.execute('SELECT state FROM sessions').fetchall()
    report['sessions']=[{'status':s['status'],'step':s['step'],'candidates':len(s.get('candidates',[])),
                         'facts_chars':len(json.dumps(s.get('facts',{}),ensure_ascii=False)),'automation':s.get('automation','workflow')}
                        for (raw,) in states for s in [json.loads(raw)]]
    records=connection.execute("SELECT payload FROM events WHERE kind='agent.tool_result' ORDER BY sequence DESC LIMIT 8").fetchall()
    report['recent_chat_tools']=[{'tool':p.get('tool'),'result_chars':len(json.dumps(p.get('result'),ensure_ascii=False))} for (raw,) in records for p in [json.loads(raw)]]
path=Path('/tmp/codex-o6-chat-diagnostics.json')
path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
