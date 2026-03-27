"""Tests for CUDA pre-flight check in photogal_entry."""
import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest


def _make_sidecar_tree(tmp_dir: str) -> str:
    """Create a fake sidecar directory with torch/lib/ and CUDA DLLs."""
    torch_lib = os.path.join(tmp_dir, "_internal", "torch", "lib")
    os.makedirs(torch_lib)
    for name in ["cudart64_12.dll", "cublas64_12.dll", "cusparse64_12.dll",
                  "cudnn64_9.dll", "c10_cuda.dll", "torch_cuda.dll"]:
        open(os.path.join(torch_lib, name), "w").close()
    open(os.path.join(tmp_dir, "cuda_installed"), "w").close()
    return torch_lib


def test_preflight_skips_non_frozen():
    """Pre-flight is a no-op when not running as frozen PyInstaller build."""
    from photogal_entry import _preflight_cuda_check
    with patch("photogal_entry.sys") as mock_sys:
        mock_sys.frozen = False
        assert _preflight_cuda_check() is True


def test_preflight_skips_non_windows():
    """Pre-flight is a no-op on macOS/Linux."""
    from photogal_entry import _preflight_cuda_check
    with patch("photogal_entry.sys") as mock_sys:
        mock_sys.frozen = True
        mock_sys.platform = "darwin"
        assert _preflight_cuda_check() is True


def test_preflight_all_dlls_ok(tmp_path):
    """When all CUDA DLLs load, pre-flight passes — no quarantine."""
    from photogal_entry import _preflight_cuda_check
    sidecar_dir = str(tmp_path)
    torch_lib = _make_sidecar_tree(sidecar_dir)

    with patch("photogal_entry.sys") as mock_sys, \
         patch("photogal_entry.ctypes") as mock_ctypes, \
         patch("photogal_entry.os.add_dll_directory", create=True):
        mock_sys.frozen = True
        mock_sys.platform = "win32"
        mock_sys.executable = os.path.join(sidecar_dir, "photogal-server-bin.exe")
        mock_sys.stderr = sys.stderr
        mock_ctypes.WinDLL.return_value = MagicMock()
        result = _preflight_cuda_check()
        assert result is True
        assert not os.path.exists(os.path.join(torch_lib, "_cuda_quarantine"))


def test_preflight_cudart_fails_quarantines(tmp_path):
    """When cudart fails to load, CUDA DLLs are quarantined + reason written."""
    from photogal_entry import _preflight_cuda_check
    sidecar_dir = str(tmp_path)
    torch_lib = _make_sidecar_tree(sidecar_dir)

    def windll_side_effect(path):
        name = os.path.basename(path) if os.sep in path or "/" in path else path
        if "cudart" in name:
            raise OSError("[WinError 126] The specified module could not be found")
        return MagicMock()

    with patch("photogal_entry.sys") as mock_sys, \
         patch("photogal_entry.ctypes") as mock_ctypes, \
         patch("photogal_entry.os.add_dll_directory", create=True):
        mock_sys.frozen = True
        mock_sys.platform = "win32"
        mock_sys.executable = os.path.join(sidecar_dir, "photogal-server-bin.exe")
        mock_sys.stderr = sys.stderr
        mock_ctypes.WinDLL.side_effect = windll_side_effect
        result = _preflight_cuda_check()
        assert result is False
        # Quarantine exists with DLLs
        quarantine = os.path.join(torch_lib, "_cuda_quarantine")
        assert os.path.isdir(quarantine)
        assert len(os.listdir(quarantine)) > 0
        # Marker removed
        assert not os.path.exists(os.path.join(sidecar_dir, "cuda_installed"))
        # Reason JSON written
        reason_path = os.path.join(sidecar_dir, "cuda_fallback_reason.json")
        assert os.path.exists(reason_path)
        data = json.loads(open(reason_path).read())
        assert data["reason"] == "cudart_load_failed"
        assert data["driver_update_helps"] is True
