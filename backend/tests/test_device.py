"""Tests for device detection, batch sizes, ONNX providers."""

from unittest.mock import patch, MagicMock

from photogal.device import (
    DeviceInfo,
    detect_capabilities,
    get_device_info,
    _reset,
    _parse_nvidia_smi,
)


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


@patch("photogal.device._parse_nvidia_smi")
@patch("photogal.device.torch")
@patch("photogal.device.platform")
def test_detect_cpu_fallback(mock_platform, mock_torch, mock_parse_smi):
    """When no GPU, backend='cpu' and dtype=float32."""
    mock_torch.cuda.is_available.return_value = False
    mock_torch.backends.mps.is_available.return_value = False
    mock_torch.float16 = "float16"
    mock_torch.float32 = "float32"
    mock_platform.machine.return_value = "x86_64"
    # _parse_nvidia_smi now returns 4 values
    mock_parse_smi.return_value = (None, None, None, None)

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


# --- _parse_nvidia_smi tests ---


@patch("photogal.device.subprocess.run")
def test_parse_nvidia_smi_full(mock_run):
    """Good --query-gpu output returns all 4 values correctly."""
    # First call: --query-gpu (structured CSV)
    query_result = MagicMock()
    query_result.returncode = 0
    query_result.stdout = "NVIDIA GeForce RTX 4070, 8.9, 535.104\n"

    # Second call: plain nvidia-smi (CUDA version from header)
    plain_result = MagicMock()
    plain_result.returncode = 0
    plain_result.stdout = (
        "| NVIDIA-SMI 535.104   Driver Version: 535.104   CUDA Version: 12.2  |\n"
        "|   0  NVIDIA GeForce RTX 4070     Off |\n"
    )

    mock_run.side_effect = [query_result, plain_result]

    gpu_name, cuda_version, driver_version, compute_capability = _parse_nvidia_smi()

    assert gpu_name == "NVIDIA GeForce RTX 4070"
    assert cuda_version == "12.2"
    assert driver_version == "535.104"
    assert compute_capability == (8, 9)


@patch("photogal.device.subprocess.run")
def test_parse_nvidia_smi_old_driver(mock_run):
    """Old driver (e.g. 472.12) parses correctly."""
    query_result = MagicMock()
    query_result.returncode = 0
    query_result.stdout = "NVIDIA GeForce GTX 1080, 6.1, 472.12\n"

    plain_result = MagicMock()
    plain_result.returncode = 0
    plain_result.stdout = (
        "| NVIDIA-SMI 472.12   Driver Version: 472.12   CUDA Version: 11.4  |\n"
    )

    mock_run.side_effect = [query_result, plain_result]

    gpu_name, cuda_version, driver_version, compute_capability = _parse_nvidia_smi()

    assert gpu_name == "NVIDIA GeForce GTX 1080"
    assert driver_version == "472.12"
    assert compute_capability == (6, 1)
    assert cuda_version == "11.4"


@patch("photogal.device.subprocess.run")
def test_parse_nvidia_smi_not_found(mock_run):
    """FileNotFoundError returns all None."""
    mock_run.side_effect = FileNotFoundError

    gpu_name, cuda_version, driver_version, compute_capability = _parse_nvidia_smi()

    assert gpu_name is None
    assert cuda_version is None
    assert driver_version is None
    assert compute_capability is None


# --- detect_capabilities CPU path tests ---


@patch("photogal.device._parse_nvidia_smi")
@patch("photogal.device.torch")
@patch("photogal.device.platform")
def test_detect_cpu_driver_too_old(mock_platform, mock_torch, mock_parse_smi):
    """Old driver version blocks upgrade with message containing '570.65'."""
    mock_torch.cuda.is_available.return_value = False
    mock_torch.backends.mps.is_available.return_value = False
    mock_torch.float16 = "float16"
    mock_torch.float32 = "float32"
    mock_platform.machine.return_value = "x86_64"
    mock_parse_smi.return_value = (
        "NVIDIA GeForce GTX 1080",
        "11.4",
        "472.12",       # driver too old (< 570.65)
        (6, 1),         # CC fine
    )

    _reset()
    info = detect_capabilities()

    assert info.backend == "cpu"
    assert info.upgrade_available is False
    assert info.upgrade_blocked_reason is not None
    assert "570.65" in info.upgrade_blocked_reason
    assert info.upgrade_fix_action is not None
    assert info.upgrade_fix_url == "https://www.nvidia.com/drivers"


@patch("photogal.device._parse_nvidia_smi")
@patch("photogal.device.torch")
@patch("photogal.device.platform")
def test_detect_cpu_gpu_too_old(mock_platform, mock_torch, mock_parse_smi):
    """CC < 5.0 blocks upgrade with no fix_url (hardware limitation)."""
    mock_torch.cuda.is_available.return_value = False
    mock_torch.backends.mps.is_available.return_value = False
    mock_torch.float16 = "float16"
    mock_torch.float32 = "float32"
    mock_platform.machine.return_value = "x86_64"
    mock_parse_smi.return_value = (
        "NVIDIA GeForce GT 730",
        "12.2",
        "572.16",       # driver fine (>= 570.65)
        (3, 5),         # CC < 5.0 — Kepler, too old
    )

    _reset()
    info = detect_capabilities()

    assert info.backend == "cpu"
    assert info.upgrade_available is False
    assert info.upgrade_blocked_reason is not None
    assert "5.0" in info.upgrade_blocked_reason
    assert info.upgrade_fix_url is None  # hardware limitation, no fix URL


@patch("photogal.device._parse_nvidia_smi")
@patch("photogal.device.torch")
@patch("photogal.device.platform")
def test_detect_cpu_good_gpu_offers_upgrade(mock_platform, mock_torch, mock_parse_smi):
    """Good driver and CC >= 5.0 → upgrade_available=True."""
    mock_torch.cuda.is_available.return_value = False
    mock_torch.backends.mps.is_available.return_value = False
    mock_torch.float16 = "float16"
    mock_torch.float32 = "float32"
    mock_platform.machine.return_value = "x86_64"
    mock_parse_smi.return_value = (
        "NVIDIA GeForce RTX 3060",
        "12.8",
        "572.16",       # driver >= 570.65
        (8, 6),         # CC >= 5.0
    )

    _reset()
    info = detect_capabilities()

    assert info.backend == "cpu"
    assert info.upgrade_available is True
    assert info.upgrade_blocked_reason is None
    assert info.gpu_name == "NVIDIA GeForce RTX 3060"
    assert info.driver_version == "572.16"
