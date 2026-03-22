"""PyInstaller entry point for photogal-server."""
import multiprocessing
multiprocessing.freeze_support()

import os
import sys


def _cleanup_stale_cuda_dlls():
    """Remove leftover CUDA DLLs in CPU-mode frozen builds (Windows only).

    When a user upgrades via NSIS installer after downloading the CUDA addon,
    the installer overwrites known files but leaves extra CUDA DLLs behind.
    PyTorch's _load_dll_libraries() scans torch/lib/ and tries to load ALL
    DLLs — including stale c10_cuda.dll — causing an OSError crash.
    """
    if not getattr(sys, 'frozen', False) or sys.platform != 'win32':
        return
    sidecar_dir = os.path.dirname(sys.executable)
    if os.path.exists(os.path.join(sidecar_dir, 'cuda_installed')):
        return  # Legitimate CUDA sidecar — keep DLLs
    torch_lib = os.path.join(sidecar_dir, '_internal', 'torch', 'lib')
    if not os.path.isdir(torch_lib):
        return
    prefixes = (
        'c10_cuda', 'torch_cuda', 'cudnn', 'cublas', 'cusparse',
        'cufft', 'curand', 'cusolver', 'nccl', 'nvrtc',
        'nvjitlink', 'caffe2_nvrtc',
    )
    for name in os.listdir(torch_lib):
        if name.endswith('.dll') and any(name.lower().startswith(p) for p in prefixes):
            try:
                os.remove(os.path.join(torch_lib, name))
                print(f'[photogal] Removed stale CUDA DLL: {name}', file=sys.stderr)
            except OSError:
                pass


_cleanup_stale_cuda_dlls()

# Redirect model caches to standard location before any library imports
from photogal.config import get_models_cache_dir

_models = get_models_cache_dir()
os.environ.setdefault("HF_HOME", str(_models / "huggingface"))
os.environ.setdefault("ARGOS_PACKAGES_DIR", str(_models / "argos"))

from photogal.cli import app

if __name__ == "__main__":
    app()
