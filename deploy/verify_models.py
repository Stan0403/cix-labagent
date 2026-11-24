import argparse
import hashlib
import json
from pathlib import Path


def verify(root):
    manifest = json.loads((Path(__file__).with_name('model_manifest.json')).read_text(encoding='utf-8'))
    errors = []
    for item in manifest['models']:
        f = root / item['path']
        if not f.is_file():
            errors.append('Missing: ' + str(f))
        elif item['sha256']:
            with f.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                    errors.append('SHA-256 mismatch: ' + str(f))
    lock = root / 'ARTIFACT_LOCK.json'
    if not lock.is_file():
        errors.append('Missing ARTIFACT_LOCK.json; run deploy/export_models.py on the source board')
    else:
        for item in json.loads(lock.read_text(encoding='utf-8')):
            f = (root / item['path']).resolve()
            if not f.is_relative_to(root.resolve()) or not f.is_file():
                errors.append('Invalid or missing locked file: ' + item['path'])
                continue
            with f.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                    errors.append('Artifact lock mismatch: ' + item['path'])
    return errors


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', type=Path, default=Path(__file__).resolve().parents[1] / 'models')
    errors = verify(parser.parse_args().models)
    if errors:
        raise SystemExit('\n'.join(errors))
    print('All required model artifacts verified')
