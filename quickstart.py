"""Create an isolated portable web environment, then start LabAgent."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-only', action='store_true')
    parser.add_argument('--port', type=int, default=8001)
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11+ required')
    if not 1 <= args.port <= 65535:
        parser.error('port must be 1..65535')
    os.chdir(ROOT)
    python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    marker = ROOT / '.venv/.delivery-installed'
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
    if not marker.exists():
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', 'requirements.lock.txt'], check=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '--no-deps', '--no-build-isolation', '-e', '.'], check=True)
        marker.write_text('portable-web-v1\n', encoding='utf-8')
    if not args.install_only:
        # Existing .env and model settings are preserved. Fresh clones use rules/manual mode.
        subprocess.run([str(python), '-m', 'labagent'], check=True,
                       env={**os.environ, 'LABAGENT_PORT': str(args.port)})


if __name__ == '__main__':
    main()
