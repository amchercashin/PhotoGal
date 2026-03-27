"""PyInstaller entry point for photogal-server."""
import multiprocessing
multiprocessing.freeze_support()

import ctypes
import json
import os
import shutil
import sys
from datetime import datetime, timezone


_CUDA_DLL_PREFIXES = (
    'c10_cuda', 'torch_cuda', 'cudnn', 'cublas', 'cublaslt',
    'cusparse', 'cufft', 'curand', 'cusolver', 'nccl', 'nvrtc',
    'nvjitlink', 'caffe2_nvrtc', 'cudart',
)

# Pre-flight probe list — order matters: first failure determines diagnosis.
# Fields: (dll_name, reason_code, message, fix_action, fix_url, driver_update_helps)
_PREFLIGHT_DLLS = [
    ("cudart64_12.dll", "cudart_load_failed",
     "Драйвер NVIDIA не поддерживает CUDA 12.8 (нужна версия ≥570.65).",
     "Обновите драйвер NVIDIA", "https://www.nvidia.com/drivers", True),
    ("cublas64_12.dll", "cublas_load_failed",
     "Ошибка загрузки CUDA-библиотеки (cublas). Возможно, сборка повреждена или антивирус заблокировал файл.",
     "Скачайте GPU-ускорение заново. Если не помогает — добавьте папку PhotoGal в исключения антивируса.",
     None, False),
    ("cusparse64_12.dll", "cusparse_load_failed",
     "Ошибка загрузки CUDA-библиотеки (cusparse). Возможно, сборка повреждена или антивирус заблокировал файл.",
     "Скачайте GPU-ускорение заново. Если не помогает — добавьте папку PhotoGal в исключения антивируса.",
     None, False),
]


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
    for name in os.listdir(torch_lib):
        if name.endswith('.dll') and any(name.lower().startswith(p) for p in _CUDA_DLL_PREFIXES):
            try:
                os.remove(os.path.join(torch_lib, name))
                print(f'[photogal] Removed stale CUDA DLL: {name}', file=sys.stderr)
            except OSError:
                pass


def _quarantine_cuda(torch_lib, sidecar_dir, reason, failed_dll, error,
                     message, fix_action, fix_url, driver_update_helps):
    """Move CUDA DLLs to quarantine and write a fallback-reason JSON."""
    quarantine_dir = os.path.join(torch_lib, '_cuda_quarantine')
    os.makedirs(quarantine_dir, exist_ok=True)

    for name in os.listdir(torch_lib):
        if name.endswith('.dll') and any(name.lower().startswith(p) for p in _CUDA_DLL_PREFIXES):
            src = os.path.join(torch_lib, name)
            dst = os.path.join(quarantine_dir, name)
            try:
                shutil.move(src, dst)
            except OSError:
                pass

    reason_data = {
        "reason": reason,
        "failed_dll": failed_dll,
        "error": str(error),
        "message": message,
        "fix_action": fix_action,
        "fix_url": fix_url,
        "driver_update_helps": driver_update_helps,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    reason_path = os.path.join(sidecar_dir, 'cuda_fallback_reason.json')
    try:
        with open(reason_path, 'w', encoding='utf-8') as f:
            json.dump(reason_data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

    marker = os.path.join(sidecar_dir, 'cuda_installed')
    try:
        os.remove(marker)
    except OSError:
        pass

    print(
        f'[photogal] CUDA pre-flight FAILED: {reason}\n'
        f'  DLL: {failed_dll}\n'
        f'  Error: {error}\n'
        f'  {message}\n'
        f'  Fix: {fix_action}',
        file=sys.stderr,
    )


def _preflight_cuda_check() -> bool:
    """Run CUDA DLL pre-flight checks before torch is imported.

    Runs only in frozen Windows builds when cuda_installed marker is present.
    Returns True if everything is OK (or check is skipped), False if quarantined.
    """
    if not getattr(sys, 'frozen', False):
        return True
    if sys.platform != 'win32':
        return True

    sidecar_dir = os.path.dirname(sys.executable)
    if not os.path.exists(os.path.join(sidecar_dir, 'cuda_installed')):
        return True  # CPU build — nothing to check

    torch_lib = os.path.join(sidecar_dir, '_internal', 'torch', 'lib')

    # Check VC++ Redistributable
    for vcrt in ('vcruntime140.dll', 'msvcp140.dll'):
        try:
            ctypes.WinDLL(vcrt)
        except OSError as e:
            _quarantine_cuda(
                torch_lib, sidecar_dir,
                'vcruntime_missing', vcrt, e,
                'Не установлен Microsoft Visual C++ Redistributable.',
                'Установите Visual C++ Redistributable 2015–2022',
                'https://aka.ms/vs/17/release/vc_redist.x64.exe',
                False,
            )
            return False

    # Check nvcuda.dll (NVIDIA driver)
    try:
        ctypes.WinDLL('nvcuda.dll')
    except OSError as e:
        _quarantine_cuda(
            torch_lib, sidecar_dir,
            'nvcuda_missing', 'nvcuda.dll', e,
            'Драйвер NVIDIA не установлен или не найден.',
            'Установите или обновите драйвер NVIDIA',
            'https://www.nvidia.com/drivers',
            True,
        )
        return False

    # Add torch/lib to DLL search path so transitive deps resolve
    if os.path.isdir(torch_lib) and hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(torch_lib)

    # Check each bundled CUDA DLL
    for dll_name, reason, message, fix_action, fix_url, driver_update_helps in _PREFLIGHT_DLLS:
        full_path = os.path.join(torch_lib, dll_name)
        try:
            ctypes.WinDLL(full_path)
        except OSError as e:
            _quarantine_cuda(
                torch_lib, sidecar_dir,
                reason, dll_name, e,
                message, fix_action, fix_url, driver_update_helps,
            )
            return False

    return True


_cleanup_stale_cuda_dlls()
_preflight_cuda_check()

# Redirect model caches to standard location before any library imports
from photogal.config import get_models_cache_dir

_models = get_models_cache_dir()
os.environ.setdefault("HF_HOME", str(_models / "huggingface"))
os.environ.setdefault("ARGOS_PACKAGES_DIR", str(_models / "argos"))

from photogal.cli import app

if __name__ == "__main__":
    app()
