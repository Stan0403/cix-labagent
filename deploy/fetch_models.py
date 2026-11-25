"""Fetch authenticated GitHub Release model assets and verify every installed file."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path(__file__).with_name('board_model_artifacts.json')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def target_path(destination, name):
    rel = PurePosixPath(name)
    if rel.is_absolute() or '..' in rel.parts or len(rel.parts) < 2 or rel.parts[0] != 'models':
        raise ValueError('Unsafe model artifact path: ' + name)
    destination = Path(destination).resolve()
    target = destination.joinpath(*rel.parts[1:]).resolve()
    if not target.is_relative_to(destination):
        raise ValueError('Model path escapes destination: ' + name)
    return target


def install_archive(archive_path, asset, destination):
    if digest(archive_path) != asset['sha256']:
        raise ValueError('Archive SHA-256 mismatch: ' + asset['name'])
    # Validate all paths and existing destinations before the first file mutation.
    with zipfile.ZipFile(archive_path) as archive:
        for row in asset['files']:
            target = target_path(destination, row['path'])
            info = archive.getinfo(row['path'])
            if info.file_size != row['bytes'] or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unexpected archive file metadata: ' + row['path'])
            if target.exists() and (not target.is_file() or digest(target) != row['sha256']):
                raise FileExistsError('A different file already exists; back it up or use a new destination: ' + str(target))
        for row in asset['files']:
            target = target_path(destination, row['path'])
            if target.exists():
                print('Verified existing:', target)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + '.partial')
            if partial.exists():
                raise FileExistsError('A previous partial transfer exists: ' + str(partial))
            try:
                with archive.open(row['path']) as source, partial.open('xb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                if partial.stat().st_size != row['bytes'] or digest(partial) != row['sha256']:
                    raise ValueError('Model SHA-256 mismatch: ' + row['path'])
                partial.replace(target)
                print('Installed and verified:', target)
            except Exception:
                if partial.is_file():
                    partial.unlink()
                raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', nargs='+', choices=['yolo', 'ocr'], default=['yolo', 'ocr'])
    parser.add_argument('--destination', type=Path, default=ROOT / 'models')
    parser.add_argument('--archive-dir', type=Path, help='Use already downloaded Release ZIPs without network access')
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--gh', default='gh', help='GitHub CLI executable path')
    args = parser.parse_args()
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    assets = [a for a in catalog['assets'] if a['model'] in args.models]
    if {a['model'] for a in assets} != set(args.models):
        raise SystemExit('Requested model is not present in the artifact catalog')
    cache = args.archive_dir or ROOT / '.tmp/model-downloads' / catalog['release_tag']
    if args.verify_only:
        for asset in assets:
            for row in asset['files']:
                target = target_path(args.destination, row['path'])
                if not target.is_file() or digest(target) != row['sha256']:
                    raise SystemExit('Missing model or SHA-256 mismatch: ' + str(target))
        print('All selected YOLO/OCR model files match the source-board export')
        return
    cache.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        archive = cache / asset['name']
        if not archive.exists():
            if args.archive_dir:
                raise SystemExit('Missing offline archive: ' + str(archive))
            partial = archive.with_suffix(archive.suffix + '.partial')
            if partial.exists():
                raise SystemExit('Remove or resume the prior partial archive before retrying: ' + str(partial))
            subprocess.run([args.gh, 'release', 'download', catalog['release_tag'], '--repo', catalog['repository'],
                            '--pattern', asset['name'], '--output', str(partial)], check=True)
            if digest(partial) != asset['sha256']:
                raise SystemExit('Downloaded archive SHA-256 mismatch; preserved for inspection: ' + str(partial))
            partial.replace(archive)
        install_archive(archive, asset, args.destination)


if __name__ == '__main__':
    main()
