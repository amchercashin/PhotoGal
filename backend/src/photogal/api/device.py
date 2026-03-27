"""Device info API — exposes GPU status to frontend."""

from __future__ import annotations

from fastapi import APIRouter

from photogal.device import get_device_info

router = APIRouter(prefix="/device", tags=["device"])


@router.get("/")
def device_status():
    info = get_device_info()

    rate_map = {"cuda": 20, "mps": 55, "cpu": 200}
    current_speed = rate_map.get(info.backend, 200)
    upgraded_speed = 20 if info.upgrade_available else current_speed

    return {
        "backend": info.backend,
        "gpu_detected": info.gpu_name,
        "gpu_backend_installed": info.gpu_backend_installed,
        "gpu_validated": info.gpu_validated,
        "compute_capability": list(info.compute_capability) if info.compute_capability else None,
        "driver_version": info.driver_version,
        "upgrade_available": info.upgrade_available,
        "upgrade_size_mb": info.upgrade_size_mb,
        "upgrade_benefit": (
            f"~{current_speed // upgraded_speed}x faster processing"
            if info.upgrade_available else None
        ),
        "upgrade_blocked_reason": info.upgrade_blocked_reason,
        "upgrade_fix_action": info.upgrade_fix_action,
        "upgrade_fix_url": info.upgrade_fix_url,
        "nvidia_cuda_version": info.nvidia_cuda_version,
        "current_speed_ms": current_speed,
        "upgraded_speed_ms": upgraded_speed,
        "clip_batch_size": info.get_optimal_batch_size("clip"),
        "face_batch_size": info.get_optimal_batch_size("face"),
        "cuda_failed": info.cuda_failed,
        "cuda_failed_reason": info.cuda_failed_reason,
        "cuda_fix_action": info.cuda_fix_action,
        "cuda_fix_url": info.cuda_fix_url,
        "cuda_driver_update_helps": info.cuda_driver_update_helps,
        "cuda_quarantined": info.cuda_quarantined,
    }
