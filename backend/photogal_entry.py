"""PyInstaller entry point for photogal-server."""
import multiprocessing
multiprocessing.freeze_support()

from photogal.cli import app

if __name__ == "__main__":
    app()
