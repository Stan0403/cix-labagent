"""Export four additional camera scenes for manual annotation, without OCR labels."""

import json
from pathlib import Path
import zipfile

rows = json.loads(Path("/tmp/codex-ocr-training-inventory/inventory.json").read_text())
selected = [r for r in rows if r["id"] in (223, 232, 290, 324)]
with zipfile.ZipFile("/tmp/codex-ocr-extra.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for row in selected:
        z.write(row["path"], f"scene-{row['id']}.jpg")
    z.writestr("sources.json", json.dumps(selected, indent=2))
print("exported", len(selected))
