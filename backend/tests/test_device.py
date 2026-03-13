"""Tests for device detection, batch sizes, ONNX providers."""

from unittest.mock import patch, MagicMock

from photogal.device import DeviceInfo, detect_capabilities, get_device_info, _reset


# --- Detection tests ---


@patch("photogal.device.torch")
def test_detect_cuda(mock_torch):
    """When CUDA available, backend='cuda' and dtype=float16."""
    mock_torch.cuda.is_available.return_value = True
    mock_torch.cuda.get_device_name.return_value = "NVIDIA RTX 4070"
    mock_torch.cuda.mem_get_info.return_value = (8_000_000_000, 8_589_934_592)
    mock_torch.cuda.get_device_capability.return_value = (8, 9)
    mock_torch.float16 = "float16"
    mock_torch.float32 = "float32"

    _reset()
    info = detect_capabilities()
    assert info.backend == "cuda"
    assert info.gpu_name == "NVIDIA RTX 4070"
    assert info.vram_mb == 8192
    assert info.dtype == "float16"
    assert info.gpu_backend_installed is True
    assert info.upgrade_available is False


@patch("photogal.device.torch")
@patch("photogal.device.platform")
def test_detect_mps(mock_platform, mock_torch):
    """When MPS available on arm64, backend='mps'."""
    mock_torch.cuda.is_available.return_value = False
    mock_torch.backends.mps.is_available.return_value = True
    mock_torch.float16 = "float16"
    mock_torch.float32 = "float32"
    mock_platform.machine.return_value = "arm64"
    mock_platform.processor.return_value = "arm"

    _reset()
    info = detect_capabilities()
    assert info.backend == "mps"
    assert info.dtype == "float16"
    assert info.vram_mb is not None
    assert info.gpu_backend_installed is True


@patch("photogal.device.torch")
@patch("photogal.device.platform")
def test_detect_cpu_fallback(mock_platform, mock_torch):
    """When no GPU, backend='cpu' and dtype=float32."""
    mock_torch.cuda.is_available.return_value = False
    mock_torch.backends.mps.is_available.return_value = False
    mock_torch.float16 = "float16"
    mock_torch.float32 = "float32"
    mock_platform.machine.return_value = "x86_64"

    _reset()
    info = detect_capabilities()
    assert info.backend == "cpu"
    assert info.dtype == "float32"
    assert info.vram_mb is None


@patch("photogal.device.torch")
def test_get_device_info_singleton(mock_torch):
    """get_device_info() returns same instance on repeated calls."""
    mock_torch.cuda.is_available.return_value = False
    mock_torch.backends.mps.is_available.return_value = False
    mock_torch.float16 = "float16"
    mock_torch.float32 = "float32"

    _reset()
    a = get_device_info()
    b = get_device_info()
    assert a is b


# --- Batch size tests ---


def test_batch_size_high_vram():
    info = DeviceInfo(
        backend="cuda", gpu_name="RTX", vram_mb=24000,
        compute_capability=(8, 9), gpu_backend_installed=True,
        upgrade_available=False, upgrade_size_mb=None, dtype=None,
    )
    assert info.get_optimal_batch_size("clip") == 128
    assert info.get_optimal_batch_size("face") == 32


def test_batch_size_mid_vram():
    info = DeviceInfo(
        backend="cuda", gpu_name="RTX", vram_mb=8192,
        compute_capability=(8, 9), gpu_backend_installed=True,
        upgrade_available=False, upgrade_size_mb=None, dtype=None,
    )
    assert info.get_optimal_batch_size("clip") == 64
    assert info.get_optimal_batch_size("face") == 16


def test_batch_size_low_vram():
    info = DeviceInfo(
        backend="cuda", gpu_name="GTX", vram_mb=4096,
        compute_capability=(7, 5), gpu_backend_installed=True,
        upgrade_available=False, upgrade_size_mb=None, dtype=None,
    )
    assert info.get_optimal_batch_size("clip") == 32
    assert info.get_optimal_batch_size("face") == 8


def test_batch_size_cpu():
    info = DeviceInfo(
        backend="cpu", gpu_name=None, vram_mb=None,
        compute_capability=None, gpu_backend_installed=False,
        upgrade_available=False, upgrade_size_mb=None, dtype=None,
    )
    assert info.get_optimal_batch_size("clip") == 8
    assert info.get_optimal_batch_size("face") == 1


# --- ONNX provider tests ---


def test_onnx_providers_cuda():
    info = DeviceInfo(
        backend="cuda", gpu_name="RTX", vram_mb=8192,
        compute_capability=None, gpu_backend_installed=True,
        upgrade_available=False, upgrade_size_mb=None, dtype=None,
    )
    assert info.get_onnx_providers() == ["CUDAExecutionProvider", "CPUExecutionProvider"]


def test_onnx_providers_mps():
    info = DeviceInfo(
        backend="mps", gpu_name="Apple M2", vram_mb=12000,
        compute_capability=None, gpu_backend_installed=True,
        upgrade_available=False, upgrade_size_mb=None, dtype=None,
    )
    assert info.get_onnx_providers() == ["CoreMLExecutionProvider", "CPUExecutionProvider"]


def test_onnx_providers_cpu():
    info = DeviceInfo(
        backend="cpu", gpu_name=None, vram_mb=None,
        compute_capability=None, gpu_backend_installed=False,
        upgrade_available=False, upgrade_size_mb=None, dtype=None,
    )
    assert info.get_onnx_providers() == ["CPUExecutionProvider"]


# --- Config tests ---


def test_config_batch_sizes_default_none():
    from photogal.config import Config
    cfg = Config()
    assert cfg.clip_batch_size_gpu is None
    assert cfg.clip_batch_size_cpu is None
