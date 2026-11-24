"""Save camera frames independently of experiment state and model inference."""

from datetime import datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from .models import DomainError


MAX_BYTES = 8_000_000


def photo_count(directory: Path) -> int:
    if not directory.exists():
        return 0
    return sum(p.is_file() and p.suffix.lower() in {".jpg", ".jpeg"} for p in directory.iterdir())


def save_photo(directory: Path, raw: bytes) -> dict:
    if len(raw) > MAX_BYTES:
        raise DomainError("照片上限 8 MB", 413)
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format != "JPEG" or image.width * image.height > 20_000_000:
                raise DomainError("请使用摄像头拍摄 JPEG 照片（不超过 2000 万像素）", 422)
            image.load()
            width, height = image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise DomainError("无法读取照片，请重拍", 422) from exc

    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{stamp}_web_{uuid4().hex[:12]}.jpg"
    # Write a unique temporary file, then publish only the complete JPEG.
    destination = directory / filename
    temporary = directory / f".{filename}.part"
    with temporary.open("xb") as stream:
        try:
            stream.write(raw)
            stream.close()
            temporary.rename(destination)
        finally:
            # In case of an I/O failure, do not leave a partial capture.
            if temporary.exists():
                temporary.unlink()
    return {"filename": filename, "count": photo_count(directory),
            "width": width, "height": height}
