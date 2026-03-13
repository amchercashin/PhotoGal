"""Thumbnail generation and caching."""

import hashlib
from pathlib import Path

from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

THUMB_SIZE = (400, 400)
THUMB_QUALITY = 85


def get_thumbnail_path(cache_dir: Path, content_hash: str | None = None, original_path: str | None = None) -> Path:
    if content_hash:
        return cache_dir / f"{content_hash[:32]}.jpg"
    key = hashlib.md5((original_path or "").encode()).hexdigest()
    return cache_dir / f"{key}.jpg"


def generate_thumbnail(
    original_path: str,
    cache_dir: Path,
    size: tuple = THUMB_SIZE,
    content_hash: str | None = None,
) -> Path:
    """Generate and cache thumbnail. Returns path to thumbnail file."""
    thumb_path = get_thumbnail_path(cache_dir, content_hash=content_hash, original_path=original_path)
    if thumb_path.exists():
        return thumb_path

    cache_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(original_path) as img:
        # Apply EXIF orientation
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img.thumbnail(size, Image.LANCZOS)
        img.save(thumb_path, "JPEG", quality=THUMB_QUALITY, optimize=True)

    return thumb_path
