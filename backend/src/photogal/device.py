"""Hardware detection and GPU device management — single source of truth."""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import subprocess
import sys
import threading
from dataclasses import dataclass

try:
    import torch
except OSError as _e:
    # CUDA DLLs bundled by mistake fail to load without NVIDIA runtime.
    # Rebuild sidecar with CPU-only torch:
    #   uv pip install torch --index-url https://download.pytorch.org/whl/cpu
    raise RuntimeError(
        f"Failed to load PyTorch: {_e}. "
        "The sidecar appears to have been built with CUDA-enabled PyTorch "
        "but the CUDA runtime is not available. "
        "Rebuild with: uv pip install torch --index-url https://download.pytorch.org/whl/cpu"
    ) from _e

logger = logging.getLogger(__name__)

_info: DeviceInfo | None = None
_lock = threading.Lock()


# Minimum driver version required for cu128 (PyTorch 2.7 CUDA 12.8) on Windows.
REQUIRED_DRIVER_VERSION = (570, 65)

# Minimum compute capability for PyTorch cu128 (Maxwell and above).
MIN_COMPUTE_CAPABILITY = (5, 0)


@dataclass
class DeviceInfo:
    backend: str                          # "cuda" | "mps" | "cpu"
    gpu_name: str | None
    vram_mb: int | None
    compute_capability: tuple | None
    gpu_backend_installed: bool
    upgrade_available: bool
    upgrade_size_mb: int | None
    dtype: torch.dtype
    gpu_validated: bool | None = None     # None = untested
    nvidia_cuda_version: str | None = None  # max CUDA version supported by driver
    upgrade_blocked_reason: str | None = None
    driver_version: str | None = None
    upgrade_fix_action: str | None = None
    upgrade_fix_url: str | None = None
    cuda_failed: bool = False
    cuda_failed_reason: str | None = None
    cuda_fix_action: str | None = None
    cuda_fix_url: str | None = None
    cuda_driver_update_helps: bool = False
    cuda_quarantined: bool = False

    def get_optimal_batch_size(self, task: str) -> int:
        """Return adaptive batch size based on available memory."""
        table = {
            "clip":  [(16384, 128), (8192, 64), (4096, 32)],
            "face":  [(16384, 32),  (8192, 16), (4096, 8)],
        }
        default = {"clip": 8, "face": 1}
        if self.vram_mb is None:
            return default.get(task, 8)
        for threshold_mb, size in table.get(task, []):
            if self.vram_mb >= threshold_mb:
                return size
        return default.get(task, 8)

    def get_onnx_providers(self) -> list[str]:
        """Return ordered list of ONNX Runtime execution providers."""
        if self.backend == "cuda":
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if self.backend == "mps":
            return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
        return ["CPUExecutionProvider"]


def _parse_nvidia_smi() -> tuple[str | None, str | None, str | None, tuple | None]:
    """Parse GPU info from nvidia-smi.

    Returns (gpu_name, cuda_version, driver_version, compute_capability).
    Any value may be None if unavailable or nvidia-smi not found.

    Uses two nvidia-smi calls:
    1. --query-gpu for structured CSV: name, compute_cap, driver_version
    2. Plain nvidia-smi to parse CUDA version via regex from header.
    """
    try:
        # Call 1: structured CSV output
        query_result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,compute_cap,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if query_result.returncode != 0:
            return None, None, None, None

        line = query_result.stdout.strip().splitlines()[0] if query_result.stdout.strip() else ""
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            return None, None, None, None

        gpu_name = parts[0] or None
        compute_cap_str = parts[1]  # e.g. "8.9"
        driver_version = parts[2] or None

        compute_capability: tuple | None = None
        if compute_cap_str:
            cap_parts = compute_cap_str.split(".")
            if len(cap_parts) == 2 and all(p.isdigit() for p in cap_parts):
                compute_capability = (int(cap_parts[0]), int(cap_parts[1]))

        # Call 2: plain nvidia-smi to parse CUDA version from header
        # Independent try/except so timeout on call 2 doesn't discard call 1 data
        cuda_version: str | None = None
        try:
            plain_result = subprocess.run(
                ["nvidia-smi"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if plain_result.returncode == 0:
                cuda_match = re.search(r"CUDA Version:\s+(\d+\.\d+)", plain_result.stdout)
                cuda_version = cuda_match.group(1) if cuda_match else None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # call 1 data preserved

        return gpu_name, cuda_version, driver_version, compute_capability

    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None, None, None


def _find_cuda_fallback_reason() -> dict | None:
    """Read cuda_fallback_reason.json from sidecar dir (frozen Windows builds only)."""
    if not getattr(sys, 'frozen', False) or sys.platform != 'win32':
        return None
    sidecar_dir = os.path.dirname(sys.executable)
    path = os.path.join(sidecar_dir, "cuda_fallback_reason.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def detect_capabilities() -> DeviceInfo:
    """Detect hardware capabilities. Not cached — use get_device_info()."""
    # 1. CUDA
    if torch.cuda.is_available():
        name = torch.cuda.get_device_name(0)
        _free, total = torch.cuda.mem_get_info(0)
        cc = torch.cuda.get_device_capability(0)
        return DeviceInfo(
            backend="cuda",
            gpu_name=name,
            vram_mb=total // (1024 * 1024),
            compute_capability=cc,
            gpu_backend_installed=True,
            upgrade_available=False,
            upgrade_size_mb=None,
            dtype=torch.float16,
        )

    # 2. MPS (Apple Silicon only)
    if torch.backends.mps.is_available() and platform.machine() == "arm64":
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            total_ram_mb = (pages * page_size) // (1024 * 1024)
            effective_mb = int(total_ram_mb * 0.75)
        except (ValueError, OSError):
            effective_mb = 8192  # safe default
        return DeviceInfo(
            backend="mps",
            gpu_name=f"Apple Silicon ({platform.processor() or 'arm64'})",
            vram_mb=effective_mb,
            compute_capability=None,
            gpu_backend_installed=True,
            upgrade_available=False,
            upgrade_size_mb=0,
            dtype=torch.float16,
        )

    # 3. CPU fallback — check if CUDA upgrade is possible
    upgrade_available = False
    upgrade_size_mb = None
    upgrade_blocked_reason = None
    upgrade_fix_action = None
    upgrade_fix_url = None
    gpu_name = None
    nvidia_cuda_version = None
    driver_version = None
    compute_capability = None

    # Detect NVIDIA GPU without CUDA PyTorch
    smi_gpu_name, nvidia_cuda_version, driver_version, compute_capability = _parse_nvidia_smi()

    if smi_gpu_name:
        gpu_name = smi_gpu_name

        # Check driver version first (pessimistic: block unless proven OK)
        driver_ok = False
        if driver_version:
            try:
                drv_parts = driver_version.split(".")
                drv_tuple = (int(drv_parts[0]), int(drv_parts[1]) if len(drv_parts) > 1 else 0)
                driver_ok = drv_tuple >= REQUIRED_DRIVER_VERSION
            except (IndexError, ValueError):
                pass

        blocked = False
        if not driver_ok:
            blocked = True
            req_str = f"{REQUIRED_DRIVER_VERSION[0]}.{REQUIRED_DRIVER_VERSION[1]}"
            upgrade_blocked_reason = (
                f"Драйвер NVIDIA ({driver_version or 'неизвестно'}) устарел, "
                f"нужна версия ≥{req_str}."
            )
            upgrade_fix_action = "Обновите драйвер NVIDIA"
            upgrade_fix_url = "https://www.nvidia.com/drivers"
            logger.warning(
                "NVIDIA GPU found but driver %s < required %s",
                driver_version or "unknown",
                req_str,
            )

        # Check compute capability
        if not blocked and compute_capability is not None:
            if compute_capability < MIN_COMPUTE_CAPABILITY:
                blocked = True
                cc_str = f"{compute_capability[0]}.{compute_capability[1]}"
                min_str = f"{MIN_COMPUTE_CAPABILITY[0]}.{MIN_COMPUTE_CAPABILITY[1]}"
                upgrade_blocked_reason = (
                    f"GPU {gpu_name} слишком старая (compute capability {cc_str}, минимум {min_str})."
                )
                # No fix_url — hardware limitation, user must replace GPU
                upgrade_fix_action = None
                upgrade_fix_url = None
                logger.warning(
                    "NVIDIA GPU %s compute capability %s < minimum %s",
                    gpu_name,
                    cc_str,
                    min_str,
                )

        # Check nvcuda.dll loadable on Windows
        if not blocked and sys.platform == "win32":
            try:
                import ctypes
                ctypes.WinDLL("nvcuda.dll")
            except OSError:
                blocked = True
                upgrade_blocked_reason = "Драйвер NVIDIA установлен некорректно."
                upgrade_fix_action = "Переустановите драйвер NVIDIA"
                upgrade_fix_url = "https://www.nvidia.com/drivers"
                logger.warning("nvcuda.dll failed to load — driver may be corrupt")

        if not blocked:
            upgrade_available = True
            upgrade_size_mb = 1500

    # Detect Apple Silicon without MPS support
    if not upgrade_available and not upgrade_blocked_reason and platform.machine() == "arm64":
        gpu_name = f"Apple Silicon ({platform.processor() or 'arm64'})"
        upgrade_available = True
        upgrade_size_mb = 0

    # Check if Layer 2 pre-flight quarantined CUDA DLLs
    fallback = _find_cuda_fallback_reason()
    cuda_failed = False
    cuda_failed_reason = None
    cuda_fix_action = None
    cuda_fix_url = None
    cuda_driver_update_helps = False
    cuda_quarantined = False

    if fallback:
        cuda_failed = True
        cuda_failed_reason = fallback.get("message")
        cuda_fix_action = fallback.get("fix_action")
        cuda_fix_url = fallback.get("fix_url")
        cuda_driver_update_helps = fallback.get("driver_update_helps", False)
        cuda_quarantined = True
        upgrade_available = False  # Don't offer download while quarantined

    return DeviceInfo(
        backend="cpu",
        gpu_name=gpu_name,
        vram_mb=None,
        compute_capability=compute_capability,
        gpu_backend_installed=False,
        upgrade_available=upgrade_available,
        upgrade_size_mb=upgrade_size_mb,
        dtype=torch.float32,
        nvidia_cuda_version=nvidia_cuda_version,
        upgrade_blocked_reason=upgrade_blocked_reason,
        driver_version=driver_version,
        upgrade_fix_action=upgrade_fix_action,
        upgrade_fix_url=upgrade_fix_url,
        cuda_failed=cuda_failed,
        cuda_failed_reason=cuda_failed_reason,
        cuda_fix_action=cuda_fix_action,
        cuda_fix_url=cuda_fix_url,
        cuda_driver_update_helps=cuda_driver_update_helps,
        cuda_quarantined=cuda_quarantined,
    )


def get_device_info() -> DeviceInfo:
    """Thread-safe cached singleton. Call this, not detect_capabilities()."""
    global _info
    if _info is not None:
        return _info
    with _lock:
        if _info is not None:
            return _info
        _info = detect_capabilities()
        logger.info(
            "Device: %s | GPU: %s | VRAM: %s MB | dtype: %s",
            _info.backend, _info.gpu_name, _info.vram_mb, _info.dtype,
        )
        return _info


def validate_gpu(info: DeviceInfo) -> bool:
    """Run a small smoke test on the detected GPU. Mutates info.gpu_validated."""
    if info.backend == "cpu":
        info.gpu_validated = True
        return True
    try:
        dummy = torch.randn(1, 768, device=info.backend, dtype=info.dtype)
        with torch.inference_mode():
            _ = dummy @ dummy.T
        if info.backend == "cuda":
            torch.cuda.synchronize()
        info.gpu_validated = True
        logger.info("GPU smoke test passed on %s", info.backend)
        return True
    except Exception as exc:
        logger.warning(
            "GPU smoke test failed on %s: %s — falling back to CPU",
            info.backend, exc,
        )
        info.backend = "cpu"
        info.dtype = torch.float32
        info.gpu_validated = False
        return False


def _reset() -> None:
    """Reset singleton — for testing only."""
    global _info
    _info = None
