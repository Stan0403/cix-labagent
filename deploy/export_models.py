"""Export existing authorized model directories with a complete SHA-256 lock."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

NAMES = ['qwen3-4b', 'qwen3-vl-4b-gguf', 'qwen3-vl-4b-npu',
         'equipment-yolo26-v1', 'sensevoice-cix', 'lcd-v3']


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--models', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    files = []
    for name in NAMES:
        folder = args.models / name
        if not folder.is_dir():
            raise SystemExit(f'Missing model directory: {folder}')
        files.extend(x for x in sorted(folder.rglob('*')) if x.is_file())
    records = []
    # Refuse to silently replace a prior export.
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for f in files:
            rel = f.relative_to(args.models).as_posix()
            with f.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            records.append({'path': rel, 'bytes': f.stat().st_size, 'sha256': digest})
            archive.write(f, 'models/' + rel)
        archive.writestr('models/ARTIFACT_LOCK.json', json.dumps(records, indent=2))
    print(f'Exported {len(records)} files: {args.output}')


if __name__ == '__main__':
    main()
