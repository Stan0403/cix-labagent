import hashlib
import zipfile

import pytest

from deploy.fetch_models import digest, install_archive, target_path


def artifact(tmp_path):
    raw = b'actual-test-model-contents'
    path = tmp_path / 'model.zip'
    name = 'models/lcd-v3/rec-lcd.onnx'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(name, raw)
    return path, {'name': path.name, 'sha256': digest(path),
                  'files': [{'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]}, raw


def test_install_and_verify_actual_archive_bytes(tmp_path):
    archive, asset, raw = artifact(tmp_path)
    install_archive(archive, asset, tmp_path / 'installed')
    assert (tmp_path / 'installed/lcd-v3/rec-lcd.onnx').read_bytes() == raw


def test_wrong_archive_digest_creates_no_models(tmp_path):
    archive, asset, _ = artifact(tmp_path)
    asset['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='Archive SHA-256'):
        install_archive(archive, asset, tmp_path / 'installed')
    assert not (tmp_path / 'installed').exists()


def test_manifest_cannot_escape_model_destination(tmp_path):
    with pytest.raises(ValueError, match='Unsafe model artifact path'):
        target_path(tmp_path / 'models', 'models/../../outside')
    assert not (tmp_path / 'outside').exists()


def test_existing_different_weights_are_preserved(tmp_path):
    archive, asset, _ = artifact(tmp_path)
    target = tmp_path / 'installed/lcd-v3/rec-lcd.onnx'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'previous-model')
    with pytest.raises(FileExistsError):
        install_archive(archive, asset, tmp_path / 'installed')
    assert target.read_bytes() == b'previous-model'
