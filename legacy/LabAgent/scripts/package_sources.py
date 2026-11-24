"""Create a shareable source ZIP without keys, runtime databases, models or virtual environments."""
import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from labagent.config import ROOT


def main():
    parser = argparse.ArgumentParser(description="Package LabAgent source only; refuses to overwrite files")
    parser.add_argument("--output", type=Path, default=ROOT.parent / "outputs" / "LabAgent_source.zip")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    directories = {"labagent", "configs", "docs", "fixtures", "knowledge", "scripts", "tests"}
    files = {"README.md", "pyproject.toml", "requirements-lock.txt", ".gitignore", ".env.example", "start.cmd"}
    selected = [ROOT / name for name in files] + [ROOT / "models" / "README.md"]
    for directory in directories:
        selected.extend(p for p in (ROOT / directory).rglob("*")
                        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"})
    with ZipFile(args.output, mode="x", compression=ZIP_DEFLATED) as archive:
        for path in sorted(selected):
            archive.write(path, "LabAgent/" + path.relative_to(ROOT).as_posix())
    print(f"Source archive: {args.output}")
    print(".env, .venv, data, models and test artifacts are intentionally excluded.")


if __name__ == "__main__":
    main()
