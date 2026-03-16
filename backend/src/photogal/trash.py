"""Move files to system Trash (cross-platform via send2trash)."""

import logging
from pathlib import Path
from send2trash import send2trash

log = logging.getLogger(__name__)


def trash_files(paths: list[str]) -> tuple[int, list[str]]:
    """Move files to system Trash.

    Returns (trashed_count, error_messages).
    """
    trashed = 0
    errors: list[str] = []

    for filepath in paths:
        p = Path(filepath)
        if not p.exists():
            continue
        try:
            send2trash(str(p))
            trashed += 1
        except Exception as e:
            log.warning("Failed to trash %s: %s", filepath, e)
            errors.append(f"{p.name}: {e}")

    return trashed, errors
