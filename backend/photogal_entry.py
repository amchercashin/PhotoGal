"""PyInstaller entry point for photogal-server."""
import multiprocessing
multiprocessing.freeze_support()

import os
import sys

# Redirect model caches to standard location before any library imports
from photogal.config import get_models_cache_dir

_models = get_models_cache_dir()
os.environ.setdefault("HF_HOME", str(_models / "huggingface"))
os.environ.setdefault("ARGOS_PACKAGES_DIR", str(_models / "argos"))

from photogal.cli import app

if __name__ == "__main__":
    app()
