"""Recover the user's direct O6 link on its currently connected Ethernet port."""

import getpass
import json
import os
from pathlib import Path
import shlex
import socket
import time

import paramiko
from o6_session import PinnedHost, execute

password = getpass.getpass('O6 SSH password: ')
deadline = time.monotonic() + 210
profile = 'codex-o6-direct-20261124'
previous = None
attempt = 0
while time.monotonic() < deadline:
    attempt += 1
    client = paramiko.SSHClient()
    client.load_host_keys(str(Path(os.environ['TEMP']) / 'codex-o6-known_hosts'))
    client.set_missing_host_key_policy(PinnedHost(client.get_host_keys(), '172.20.10.2'))
    try:
        client.connect('fe80::e70:98f6:cb91:f9a8%11', username='radxa', password=password,
                       look_for_keys=False, allow_agent=False, timeout=3, banner_timeout=4, auth_timeout=4)
        before = execute(client, 'ip -br addr; cat /sys/class/net/enp49s0/address; nmcli -g GENERAL.CONNECTION device show enp49s0', password, timeout=8)
        assert before['exit'] == 0 and '00:48:54:20:1d:14' in before['out'], before
        previous = before['out']
        print('PINNED_O6_CONNECTED', previous, flush=True)
        command = (f'nmcli con show {shlex.quote(profile)} >/dev/null 2>&1 || '
                   f'nmcli con add type ethernet ifname enp49s0 con-name {shlex.quote(profile)} '
                   'connection.autoconnect no ipv4.method manual ipv4.addresses 192.168.123.2/24 '
                   'ipv4.never-default yes ipv6.method link-local; '
                   f'nmcli con up {shlex.quote(profile)}')
        try:
            print(execute(client, command, password, sudo=True, timeout=15), flush=True)
        except (OSError, EOFError):
            print('Configuration sent; checking direct IPv4', flush=True)
        for _ in range(10):
            try:
                with socket.create_connection(('192.168.123.2', 22), timeout=2):
                    out = Path(__file__).resolve().parents[1] / 'npu_llm/2026-11-24/network-recovery.json'
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps({'connected_host': '192.168.123.2', 'interface': 'enp49s0',
                        'profile': profile, 'previous': previous, 'autoconnect': False,
                        'wifi_changed': False}, indent=2))
                    print('DIRECT_IPV4_READY', flush=True)
                    raise SystemExit(0)
            except OSError:
                time.sleep(1)
    except paramiko.AuthenticationException:
        raise SystemExit('Provided radxa credentials were rejected')
    except (OSError, paramiko.SSHException) as exc:
        print('Waiting for board DHCP/link-local window', attempt, type(exc).__name__, flush=True)
    finally:
        client.close()
    time.sleep(1)
raise SystemExit('Board link-local SSH did not become available in the bounded recovery window')
