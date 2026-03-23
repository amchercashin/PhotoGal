"""Hardware detection and GPU device management — single source of truth."""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
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


# Minimum CUDA version required by the CUDA sidecar.
# Update this when rebuilding the CUDA sidecar with a different PyTorch CUDA variant.
# cu128 is needed for Blackwell (RTX 50xx, sm_120) support.
REQUIRED_CUDA_VERSION = (12, 8)


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


def _parse_nvidia_smi() -> tuple[str | None, str | None]:
    """Parse GPU name and max CUDA version from a single nvidia-smi call.

    Returns (gpu_name, cuda_version) — either may be None.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None, None
        output = result.stdout
        # CUDA version from header: "| NVIDIA-SMI 535.104   Driver Version: 535.104   CUDA Version: 12.2  |"
        cuda_match = re.search(r"CUDA Version:\s+(\d+\.\d+)", output)
        cuda_ver = cuda_match.group(1) if cuda_match else None
        # GPU name from table row: "|   0  NVIDIA GeForce RTX 5060     Off |"
        gpu_name = None
        name_match = re.search(r"\|\s+\d+\s+(.+?)\s+(?:On|Off)\s*\|", output)
        if name_match:
            gpu_name = name_match.group(1).strip()
        return gpu_name, cuda_ver
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None


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

    # 3. CPU fallback — check if upgrade is possible
    upgrade_available = False
    upgrade_size_mb = None
    upgrade_blocked_reason = None
    gpu_name = None
    nvidia_cuda_version = None

    # Detect NVIDIA GPU without CUDA PyTorch (single nvidia-smi call)
    smi_gpu_name, nvidia_cuda_version = _parse_nvidia_smi()
    if smi_gpu_name:
        gpu_name = smi_gpu_name
        if nvidia_cuda_version:
            driver_cuda = tuple(int(x) for x in nvidia_cuda_version.split("."))
            if driver_cuda >= REQUIRED_CUDA_VERSION:
                upgrade_available = True
                upgrade_size_mb = 1500
            else:
                upgrade_blocked_reason = (
                    f"Драйвер поддерживает CUDA {nvidia_cuda_version}, "
                    f"требуется ≥ {'.'.join(map(str, REQUIRED_CUDA_VERSION))}. "
                    "Обновите драйвер NVIDIA."
                )
                logger.warning(
                    "NVIDIA GPU found but driver CUDA %s < required %s",
                    nvidia_cuda_version,
                    ".".join(map(str, REQUIRED_CUDA_VERSION)),
                )
        else:
            # Can't determine CUDA version — offer upgrade optimistically
            upgrade_available = True
            upgrade_size_mb = 1500

    # Detect Apple Silicon without MPS support
    if not upgrade_available and not upgrade_blocked_reason and platform.machine() == "arm64":
        gpu_name = f"Apple Silicon ({platform.processor() or 'arm64'})"
        upgrade_available = True
        upgrade_size_mb = 0

    return DeviceInfo(
        backend="cpu",
        gpu_name=gpu_name,
        vram_mb=None,
        compute_capability=None,
        gpu_backend_installed=False,
        upgrade_available=upgrade_available,
        upgrade_size_mb=upgrade_size_mb,
        dtype=torch.float32,
        nvidia_cuda_version=nvidia_cuda_version,
        upgrade_blocked_reason=upgrade_blocked_reason,
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
