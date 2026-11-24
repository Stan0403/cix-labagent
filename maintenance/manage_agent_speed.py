"""Stage and atomically deploy only hash-matched Agent optimization files."""
import hashlib
import json
import os
from pathlib import Path
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timedelta, timezone
import urllib.request

APP=Path('/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo')
WORK=Path('/tmp/codex-agent-speed')
CANDIDATE=WORK/'candidate'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def child(root, name):
    path=(root/name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError('Unsafe path')
    return path

def prepare():
    CANDIDATE.mkdir(parents=True,exist_ok=True)
    with tarfile.open(WORK/'candidate.tar.gz') as archive:
        for member in archive:
            if not member.isfile():
                raise ValueError('Only regular source files allowed')
            target=child(CANDIDATE,member.name)
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    manifest=json.loads((CANDIDATE/'optimization-manifest.json').read_text())
    for entry in manifest['files']:
        source=child(CANDIDATE,entry['path'])
        if digest(source)!=entry['sha256']:
            raise ValueError('Candidate hash mismatch')
        if source.suffix=='.py':
            py_compile.compile(str(source),doraise=True)
    print('CANDIDATE_READY',len(manifest['files']),flush=True)

def healthy():
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(30):
        try:
            with opener.open('http://127.0.0.1:8001/api/health',timeout=2) as response:
                if json.load(response).get('capabilities',{}).get('agent_controller')=='llm':
                    return True
        except (OSError,ValueError):
            pass
        time.sleep(.3)
    return False

def database_counts():
    path=APP/'data/labagent.sqlite3'
    if not path.is_file():
        return None
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        return {name:db.execute('SELECT count(*) FROM '+name).fetchone()[0]
                for name in ['sessions','events','messages','agent_runs']}

def deploy():
    report=json.loads((WORK/'after/benchmark.json').read_text())
    if not report.get('contract_passed') or len(report.get('cases',[]))<7:
        raise RuntimeError('Real model contract has not passed')
    for name, count in [('cold-after',1),('native-after',2)]:
        extra=json.loads((WORK/name/'benchmark.json').read_text())
        if not extra.get('contract_passed') or len(extra.get('cases',[]))<count:
            raise RuntimeError('Additional model contract has not passed: '+name)
        if name=='cold-after' and not extra.get('cold_cache_verified'):
            raise RuntimeError('Cold cache measurement was not verified')
    tests=WORK/'board-tests.xml'
    if not tests.is_file():
        raise RuntimeError('Board tests missing')
    from xml.etree import ElementTree
    tree=ElementTree.parse(tests)
    suites=[tree.getroot()] if tree.getroot().tag=='testsuite' else tree.findall('testsuite')
    if not suites or any(int(s.get('failures','0')) or int(s.get('errors','0')) for s in suites):
        raise RuntimeError('Board tests failed')
    dbpath=APP/'data/labagent.sqlite3'
    if dbpath.is_file():
        with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True) as db:
            cutoff=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()
            if db.execute("SELECT count(*) FROM agent_runs WHERE status='executing' AND created_at>?",(cutoff,)).fetchone()[0]:
                raise RuntimeError('An experiment tool is currently executing; retry when idle')
    manifest=json.loads((CANDIDATE/'optimization-manifest.json').read_text())
    for entry in manifest['files']:
        if digest(child(CANDIDATE,entry['path']))!=entry['sha256']:
            raise RuntimeError('Candidate changed after validation')
        if digest(child(APP,entry['path'])) not in {entry['baseline_sha256'],entry['sha256']}:
            raise RuntimeError('Production file changed concurrently: '+entry['path'])
    backup=APP/'.runtime'/('agent-speed-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    before_counts=database_counts()
    backup.mkdir(parents=True,exist_ok=False)
    (backup/'manifest.json').write_text(json.dumps(manifest,indent=2))
    absent=[]
    for entry in manifest['files']:
        source=child(APP,entry['path']);target=child(backup,entry['path'])
        if source.exists():
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        else:
            absent.append(entry['path'])
    (backup/'absent.json').write_text(json.dumps(absent))
    try:
        for entry in manifest['files']:
            target=child(APP,entry['path']);target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name(target.name+'.codex-agent-speed')
            shutil.copy2(child(CANDIDATE,entry['path']),temporary)
            os.replace(temporary,target)
        subprocess.run(['systemctl','--user','restart','labagent-demo-web.service'],check=True,timeout=40)
        if not healthy():
            raise RuntimeError('Web health check failed')
        result={'activated':True,'backup':str(backup),'files':manifest['files'],
                'service_restarted':'labagent-demo-web.service',
                'database_counts_before':before_counts,'database_counts_after':database_counts()}
        (WORK/'deployment.json').write_text(json.dumps(result,indent=2))
        print('ACTIVATED',backup,flush=True)
    except Exception:
        for entry in manifest['files']:
            target=child(APP,entry['path'])
            if entry['path'] in absent:
                target.unlink(missing_ok=True)
            else:
                shutil.copy2(child(backup,entry['path']),target)
        subprocess.run(['systemctl','--user','restart','labagent-demo-web.service'],check=True,timeout=40)
        print('ROLLED_BACK',backup,flush=True)
        raise

if __name__=='__main__':
    {'prepare':prepare,'deploy':deploy}[sys.argv[1]]()
