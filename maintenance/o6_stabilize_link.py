"""Recover the authorized direct development link without changing Wi-Fi profiles."""
import getpass
import json
import os
from pathlib import Path
import socket
import time

import paramiko
from o6_session import PinnedHost

password=getpass.getpass('O6 SSH password: ')
profile='codex-o6-direct-20261122'
command=(f'nmcli con show {profile} >/dev/null 2>&1 || '
         f'nmcli con add type ethernet ifname enp1s0 con-name {profile} connection.autoconnect no '
         'ipv4.method manual ipv4.addresses 192.168.123.2/24 ipv4.never-default yes ipv6.method link-local; '
         f'nmcli con up {profile}')
deadline=time.monotonic()+180
attempt=0
while time.monotonic()<deadline:
    attempt+=1
    client=paramiko.SSHClient()
    client.load_host_keys(str(Path(os.environ['TEMP'])/'codex-o6-known_hosts'))
    client.set_missing_host_key_policy(PinnedHost(client.get_host_keys(),'172.20.10.2'))
    try:
        client.connect('fe80::221b:4331:65d6:d120%12',username='radxa',password=password,
                       look_for_keys=False,allow_agent=False,timeout=3,banner_timeout=5,auth_timeout=5)
        print('CONNECTED',flush=True)
        stdin,stdout,stderr=client.exec_command('nmcli -g GENERAL.STATE,GENERAL.CONNECTION device show enp1s0',timeout=5)
        before=stdout.read().decode()
        print('BEFORE',before.strip(),flush=True)
        stdin,stdout,stderr=client.exec_command("sudo -S -p '' -- sh -c "+__import__('shlex').quote(command),timeout=15)
        stdin.write(password+'\n');stdin.flush();stdin.channel.shutdown_write()
        print('CONFIGURATION_SENT',flush=True)
        try:
            print(stdout.read().decode(),stderr.read().decode(),flush=True)
        except (OSError,EOFError,socket.timeout):
            print('Expected possible disconnect while switching the wired connection',flush=True)
        for _ in range(8):
            try:
                with socket.create_connection(('192.168.123.2',22),timeout=2):
                    report={'profile':profile,'address':'192.168.123.2','previous':before,
                            'autoconnect':False,'wifi_profiles_changed':False}
                    out=Path(__file__).resolve().parents[1]/'agent_optimization/2026-11-22/network.json'
                    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
                    print('DIRECT_IPV4_READY',flush=True)
                    raise SystemExit(0)
            except OSError:
                time.sleep(1)
    except (OSError,paramiko.SSHException) as exc:
        print('RETRY',attempt,type(exc).__name__,flush=True)
    finally:
        client.close()
    time.sleep(2)
raise SystemExit('Direct link did not become stable within the bounded retry window')
