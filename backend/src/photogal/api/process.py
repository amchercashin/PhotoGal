"""Process API: run pipeline levels, track progress."""

import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from photogal.api.deps import get_db
from photogal.config import load_config
from photogal.db import Database

router = APIRouter(prefix="/process", tags=["process"])

# Global pipeline state (simple threading approach)
_pipeline_state: dict = {
    "running": False,
    "level": None,
    "source_id": None,
    "progress": 0,
    "total": 0,
    "stage": None,
    "started_at": None,
    "stage_started_at": None,
    "error": None,
}
_pipeline_lock = threading.Lock()
_pipeline_thread: threading.Thread | None = None


def _pipeline_stage_cb(stage: str, total: int):
    with _pipeline_lock:
        _pipeline_state["stage"] = stage
        _pipeline_state["total"] = total
        _pipeline_state["progress"] = 0
        _pipeline_state["stage_started_at"] = datetime.now().isoformat()


def _pipeline_progress_cb(done: int):
    with _pipeline_lock:
        _pipeline_state["progress"] = done


@router.get("/status")
def get_status():
    with _pipeline_lock:
        state = dict(_pipeline_state)
    # Compute elapsed times outside lock
    elapsed_s = 0.0
    stage_elapsed_s = 0.0
    if state["running"]:
        if state["started_at"]:
            elapsed_s = (datetime.now() - datetime.fromisoformat(state["started_at"])).total_seconds()
        if state.get("stage_started_at"):
            stage_elapsed_s = (datetime.now() - datetime.fromisoformat(state["stage_started_at"])).total_seconds()
    state["elapsed_s"] = round(elapsed_s, 1)
    state["stage_elapsed_s"] = round(stage_elapsed_s, 1)
    state.pop("stage_started_at", None)  # internal field
    return state


class RunLevelRequest(BaseModel):
    level: int  # 0, 1, or 2
    source_id: int | None = None  # required for level 0


@router.post("/run")
def run_level(req: RunLevelRequest, db: Database = Depends(get_db)):
    global _pipeline_thread

    with _pipeline_lock:
        if _pipeline_state["running"]:
            raise HTTPException(status_code=409, detail="Pipeline already running")

        if req.level not in (0, 1, 2, 3):
            raise HTTPException(status_code=400, detail="Level must be 0, 1, 2, or 3")

        if req.level == 0 and req.source_id is None:
            raise HTTPException(status_code=400, detail="source_id required for level 0")

        _pipeline_state.update({
            "running": True,
            "level": req.level,
            "source_id": req.source_id,
            "progress": 0,
            "total": 0,
            "stage": "starting",
            "started_at": datetime.now().isoformat(),
            "stage_started_at": datetime.now().isoformat(),
            "error": None,
        })

    config = load_config()

    def _run():
        try:
            _execute_level(req.level, req.source_id, db, config)
        except Exception as e:
            with _pipeline_lock:
                _pipeline_state["error"] = str(e)
        finally:
            with _pipeline_lock:
                _pipeline_state["running"] = False
                _pipeline_state["stage"] = "idle"

    _pipeline_thread = threading.Thread(target=_run, daemon=True)
    _pipeline_thread.start()

    return {"ok": True, "level": req.level}


@router.post("/stop")
def stop_pipeline():
    with _pipeline_lock:
        if not _pipeline_state["running"]:
            return {"ok": True, "message": "Not running"}
        _pipeline_state["stage"] = "stopping"
    return {"ok": True}


class RunMarkedRequest(BaseModel):
    photo_ids: list[int]
    target_level: int = 3  # 2 = stop at CLIP, 3 = include faces


@router.post("/run-marked")
def run_marked(req: RunMarkedRequest, db: Database = Depends(get_db)):
    global _pipeline_thread

    with _pipeline_lock:
        if _pipeline_state["running"]:
            raise HTTPException(status_code=409, detail="Pipeline already running")
        if not req.photo_ids:
            raise HTTPException(status_code=400, detail="photo_ids is empty")

        _pipeline_state.update({
            "running": True,
            "level": req.target_level,
            "source_id": None,
            "progress": 0,
            "total": len(req.photo_ids),
            "stage": "starting",
            "started_at": datetime.now().isoformat(),
            "stage_started_at": datetime.now().isoformat(),
            "error": None,
        })

    config = load_config()

    def _run():
        try:
            # Filter out disconnected photos
            all_photos = db.get_photos_by_ids(req.photo_ids)
            active_ids = [
                p["id"] for p in all_photos
                if (p["sync_status"] if "sync_status" in p.keys() else "ok") != "disconnected"
            ]

            from photogal.pipeline.analyzer import Analyzer
            Analyzer(config).run_for_ids(
                db, active_ids,
                progress_callback=_pipeline_progress_cb,
                stage_callback=_pipeline_stage_cb,
            )

            # L3: face analysis (if requested)
            if req.target_level >= 3:
                placeholders = ",".join("?" * len(active_ids))
                l2_rows = db.conn.execute(
                    f"SELECT id FROM photos WHERE id IN ({placeholders}) AND processing_level = 2",
                    active_ids,
                ).fetchall()
                l2_ids = [r["id"] for r in l2_rows]
                if l2_ids:
                    from photogal.pipeline.face_analyzer import FaceAnalyzer
                    fa = FaceAnalyzer()
                    fa.detect_faces(db, l2_ids,
                                    progress_callback=_pipeline_progress_cb,
                                    stage_callback=_pipeline_stage_cb)
                    db.update_photos_batch(
                        ["processing_level"],
                        [(3, pid) for pid in l2_ids],
                    )
                    db.commit()
                    fa.cluster_faces(db,
                                     progress_callback=_pipeline_progress_cb,
                                     stage_callback=_pipeline_stage_cb)

            with _pipeline_lock:
                _pipeline_state["stage"] = "done"
                _pipeline_state["progress"] = _pipeline_state["total"]

        except Exception as e:
            with _pipeline_lock:
                _pipeline_state["error"] = str(e)
        finally:
            with _pipeline_lock:
                _pipeline_state["running"] = False
                if _pipeline_state["stage"] != "done":
                    _pipeline_state["stage"] = "idle"

    _pipeline_thread = threading.Thread(target=_run, daemon=True)
    _pipeline_thread.start()
    return {"ok": True}


def _execute_level(level: int, source_id: int | None, db: Database, config):
    if level == 0:
        from photogal.pipeline.scanner import Scanner, discover_files
        source = db.get_source(source_id)
        if not source:
            raise ValueError(f"Source {source_id} not found")
        scan_path = Path(source["path"])

        files = discover_files(scan_path, config.supported_extensions)
        with _pipeline_lock:
            _pipeline_state["total"] = len(files)
            _pipeline_state["stage"] = "scanning"
            _pipeline_state["stage_started_at"] = datetime.now().isoformat()

        scanner = Scanner(config, max_workers=config.max_workers)
        result = scanner.run(db, source_id, scan_path,
                             pre_discovered_files=files,
                             progress_callback=_pipeline_progress_cb)

        # ── Auto-chain: clustering + geocoding ──────────────────────────
        with _pipeline_lock:
            if _pipeline_state.get("stage") == "stopping":
                return

        rows = db.conn.execute(
            "SELECT id FROM photos WHERE processing_level = 0 AND source_id = ?",
            (source_id,),
        ).fetchall()
        l1_ids = [r["id"] for r in rows]

        if not l1_ids:
            with _pipeline_lock:
                _pipeline_state["stage"] = "done"
                _pipeline_state["progress"] = result["scanned"]
                _pipeline_state["total"] = result["scanned"]
            return

        with _pipeline_lock:
            _pipeline_state["level"] = 1
            _pipeline_state["progress"] = 0
            _pipeline_state["total"] = len(l1_ids)

        l1_photos = db.get_photos_by_ids(l1_ids)

        from photogal.pipeline.analyzer import Analyzer
        analyzer = Analyzer(config)
        l1_result = analyzer._run_l1(
            db, l1_photos, scoped=True,
            progress_callback=_pipeline_progress_cb,
            stage_callback=_pipeline_stage_cb,
        )

        with _pipeline_lock:
            _pipeline_state["stage"] = "done"
            _pipeline_state["progress"] = l1_result["processed"]
            _pipeline_state["total"] = l1_result["processed"]

    elif level == 1:
        from photogal.pipeline.analyzer import Analyzer

        analyzer = Analyzer(config)
        result = analyzer.run(db,
                              progress_callback=_pipeline_progress_cb,
                              stage_callback=_pipeline_stage_cb)
        with _pipeline_lock:
            _pipeline_state["stage"] = "done"
            _pipeline_state["progress"] = result["processed"]
            _pipeline_state["total"] = result["processed"]

    elif level == 2:
        from photogal.pipeline.analyzer import Analyzer

        analyzer = Analyzer(config)
        result = analyzer.run_clip(
            db,
            progress_callback=_pipeline_progress_cb,
            stage_callback=_pipeline_stage_cb,
        )
        with _pipeline_lock:
            _pipeline_state["stage"] = "done"
            _pipeline_state["progress"] = result["processed"]
            _pipeline_state["total"] = result["processed"]

    elif level == 3:
        from photogal.pipeline.face_analyzer import FaceAnalyzer

        analyzer = FaceAnalyzer()
        result = analyzer.run(
            db,
            progress_callback=_pipeline_progress_cb,
            stage_callback=_pipeline_stage_cb,
        )
        with _pipeline_lock:
            _pipeline_state["stage"] = "done"
            _pipeline_state["progress"] = result["processed"]
            _pipeline_state["total"] = result["processed"]


class EstimateRequest(BaseModel):
    photo_count: int


@router.post("/estimate")
def estimate_time(req: EstimateRequest, db: Database = Depends(get_db)):
    """Estimate processing time for L2 (CLIP) analysis."""
    if req.photo_count <= 0:
        return {"estimated_seconds": 0, "rate_per_photo_ms": 0, "source": "none"}

    # Historical rate from perf_log (last 5 embedding runs)
    row = db.conn.execute(
        "SELECT AVG(avg_rate) as avg_rate FROM ("
        "  SELECT duration_s / NULLIF(items, 0) as avg_rate "
        "  FROM perf_log "
        "  WHERE stage = 'analyze/embeddings' AND items > 0 "
        "  ORDER BY created_at DESC LIMIT 5"
        ")"
    ).fetchone()

    if row and row["avg_rate"] and row["avg_rate"] > 0:
        rate = row["avg_rate"]
        source = "historical"
    else:
        try:
            from photogal.device import get_device_info
            info = get_device_info()
            rate_map = {"cuda": 0.020, "mps": 0.055, "cpu": 0.200}
            rate = rate_map.get(info.backend, 0.200)
            source = f"default_{info.backend}"
        except Exception:
            rate = 0.200
            source = "default_cpu"

    return {
        "estimated_seconds": round(rate * req.photo_count, 1),
        "rate_per_photo_ms": round(rate * 1000, 1),
        "source": source,
    }
