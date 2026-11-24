"""Copy source-only ZIP through SSH, leaving board environments, data and models intact."""
import argparse
import base64
import io
import shlex
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="radxa@172.20.10.2")
    parser.add_argument("--remote", default="/home/radxa/referenced-chatgpt-conversation-this-is-an/LabAgent-demo")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    buffer = io.BytesIO()
    allowed = {"labagent", "scripts", "configs", "knowledge", "fixtures", "tests", "docs"}
    files = {"README.md", "pyproject.toml", ".env.example", ".gitignore"}
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if rel.parts[0] not in allowed and rel.as_posix() not in files:
                continue
            if any(p in {"__pycache__", ".pytest_cache"} for p in rel.parts) or path.suffix == ".pyc":
                continue
            archive.write(path, rel.as_posix())
    code = ("import base64,io,sys,zipfile;from pathlib import Path;"
            f"root=Path({args.remote!r}).resolve();root.mkdir(parents=True,exist_ok=True);"
            "z=zipfile.ZipFile(io.BytesIO(base64.b64decode(sys.stdin.buffer.read())));"
            "members=z.infolist();"
            "assert all(not x.filename.startswith('/') and '..' not in Path(x.filename).parts for x in members);"
            "z.extractall(root);print('SOURCE_SYNC_OK',len(members))")
    command = ["ssh", "-T", "-i", str(Path.home() / ".ssh/o6_labagent"), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=yes", args.host, "python3 -c " + shlex.quote(code)]
    subprocess.run(command, input=base64.b64encode(buffer.getvalue()), check=True)


if __name__ == "__main__":
    main()
