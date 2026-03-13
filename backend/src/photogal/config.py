"""Configuration and constants."""

from dataclasses import dataclass, field
from pathlib import Path

IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".heic", ".heif",
    ".tiff", ".tif", ".webp", ".bmp",
    ".raw", ".cr2", ".nef", ".arw", ".dng",
})


@dataclass
class Config:
    """Application configuration."""

    # Scanner
    hash_buffer_size: int = 65536
    phash_hamming_threshold: int = 8
    batch_size: int = 500
    max_workers: int | None = None

    # Level 2 clustering thresholds
    similarity_max_distance_m: float = 50.0      # max GPS distance in meters
    similarity_max_time_delta_s: float = 180.0   # max time delta in seconds (3 min)

    # DB
    db_filename: str = "photogal.db"

    # CLIP
    clip_model: str = "ViT-L-14"
    clip_pretrained: str = "laion2b_s32b_b82k"
    clip_batch_size_gpu: int | None = None
    clip_batch_size_cpu: int | None = None

    # Quality thresholds
    blur_threshold: float = 500.0
    exposure_dark_threshold: float = 50.0
    exposure_bright_threshold: float = 220.0

    # Thumbnail
    thumbnail_size: tuple[int, int] = (400, 400)
    thumbnail_cache_dir: str = ".thumbnails"

    supported_extensions: frozenset[str] = field(default_factory=lambda: IMAGE_EXTENSIONS)


def load_config() -> Config:
    return Config()


def get_db_path() -> Path:
    """Default DB path: ~/.photogal/photogal.db"""
    data_dir = Path.home() / ".photogal"
    data_dir.mkdir(exist_ok=True)
    return data_dir / "photogal.db"


def get_thumbnail_cache_dir() -> Path:
    """Default thumbnail cache: ~/.photogal/.thumbnails/"""
    d = Path.home() / ".photogal" / ".thumbnails"
    d.mkdir(parents=True, exist_ok=True)
    return d
