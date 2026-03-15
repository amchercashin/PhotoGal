"""PyInstaller entry point for photogal-server."""
import multiprocessing
multiprocessing.freeze_support()

import os
import sys
from pathlib import Path

# Redirect model caches to macOS-standard location before any library imports
if sys.platform == "darwin":
    _cache = Path.home() / "Library" / "Caches" / "com.photogal.desktop" / "models"
    os.environ.setdefault("HF_HOME", str(_cache / "huggingface"))
    os.environ.setdefault("ARGOS_PACKAGES_DIR", str(_cache / "argos"))

from photogal.cli import app

if __name__ == "__main__":
    app()
