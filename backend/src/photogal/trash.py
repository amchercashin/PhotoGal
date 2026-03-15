"""Move files to system Trash (macOS)."""

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)


def trash_files(paths: list[str]) -> tuple[int, list[str]]:
    """Move files to system Trash.

    Returns (trashed_count, error_messages).
    """
    trash_dir = Path.home() / ".Trash"
    trashed = 0
    errors: list[str] = []

    for filepath in paths:
        p = Path(filepath)
        if not p.exists():
            # File already gone — not an error, just skip
            continue
        try:
            dest = trash_dir / p.name
            # Handle name collision in Trash
            if dest.exists():
                stem, suffix = p.stem, p.suffix
                counter = 1
                while dest.exists():
                    dest = trash_dir / f"{stem} {counter}{suffix}"
                    counter += 1
            shutil.move(str(p), str(dest))
            trashed += 1
        except Exception as e:
            log.warning("Failed to trash %s: %s", filepath, e)
            errors.append(f"{p.name}: {e}")

    return trashed, errors
