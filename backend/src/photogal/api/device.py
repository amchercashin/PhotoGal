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
        "upgrade_available": info.upgrade_available,
        "upgrade_size_mb": info.upgrade_size_mb,
        "upgrade_benefit": (
            f"~{current_speed // upgraded_speed}x faster processing"
            if info.upgrade_available else None
        ),
        "current_speed_ms": current_speed,
        "upgraded_speed_ms": upgraded_speed,
        "clip_batch_size": info.get_optimal_batch_size("clip"),
        "face_batch_size": info.get_optimal_batch_size("face"),
    }
