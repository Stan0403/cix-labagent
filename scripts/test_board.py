"""Run regressions using the existing board audio/vision libraries without reinstalling them."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
original = root.parent / "LabAgent"
for name in [".venv-ocr", ".venv-asr"]:
    directory = original / name / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    if directory.is_dir():
        sys.path.append(str(directory))
sys.path.insert(0, str(root))
import pytest  # noqa: E402

raise SystemExit(pytest.main(["-q", "-p", "no:cacheprovider", "--junitxml=test-results/board-junit.xml", *sys.argv[1:]]))
