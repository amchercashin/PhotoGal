"""Sync API: check which photos are still on disk."""

import os
import threading

from fastapi import APIRouter, Depends

from photogal.api.deps import get_db
from photogal.db import Database

router = APIRouter(prefix="/sync", tags=["sync"])

_sync_state: dict = {
    "running": False,
    "checked": 0,
    "total": 0,
    "disconnected": 0,
}
_sync_lock = threading.Lock()


@router.get("/status")
def get_sync_status():
    with _sync_lock:
        return dict(_sync_state)


@router.post("/check")
def trigger_sync_check(db: Database = Depends(get_db)):
    with _sync_lock:
        if _sync_state["running"]:
            return {"ok": True, "message": "already running"}

    def _run():
        pairs = db.get_all_photo_paths()
        total = len(pairs)
        with _sync_lock:
            _sync_state.update({"running": True, "checked": 0, "total": total, "disconnected": 0})

        updates: list[tuple[str, int]] = []
        disconnected = 0
        for i, (photo_id, path) in enumerate(pairs):
            status = "ok" if os.path.exists(path) else "disconnected"
            updates.append((status, photo_id))
            if status == "disconnected":
                disconnected += 1
            if i % 500 == 0:
                with _sync_lock:
                    _sync_state["checked"] = i

        if updates:
            db.update_sync_status_bulk(updates)
            db.commit()

        with _sync_lock:
            _sync_state.update({
                "running": False,
                "checked": total,
                "disconnected": disconnected,
            })

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"ok": True}
