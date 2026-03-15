"""Process API: run pipeline levels, track progress."""

import dataclasses
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from photogal.api.deps import get_db
from photogal.config import load_config
from photogal.db import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/process", tags=["process"])


@dataclass
class PipelineState:
    running: bool = False
    level: int | None = None
    source_id: int | None = None
    progress: int = 0
    total: int = 0
    stage: str | None = None
    started_at: float | None = None
    stage_started_at: str | None = None
    error: str | None = None


class PipelineScheduler:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = PipelineState()

    def start(self, run_fn, level, source_id=None, total=0):
        """Atomically initialize state and start a pipeline run in a background thread.

        All state setup happens inside a single lock acquisition to prevent
        race conditions where state is set but the thread fails to start.
        """
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise HTTPException(status_code=409, detail="Pipeline already running")
            self._stop_event.clear()
            self._state = PipelineState(
                running=True,
                level=level,
                source_id=source_id,
                progress=0,
                total=total,
                stage="starting",
                started_at=time.time(),
                stage_started_at=datetime.now().isoformat(),
                error=None,
            )
            self._thread = threading.Thread(target=run_fn, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=30)

    def get_status(self) -> dict:
        with self._lock:
            s = dataclasses.replace(self._state)
        d = dataclasses.asdict(s)
        # Compute elapsed times outside lock
        elapsed_s = 0.0
        stage_elapsed_s = 0.0
        if s.running:
            if s.started_at:
                elapsed_s = time.time() - s.started_at
            if s.stage_started_at:
                stage_elapsed_s = (
                    datetime.now() - datetime.fromisoformat(s.stage_started_at)
                ).total_seconds()
        d["elapsed_s"] = round(elapsed_s, 1)
        d["stage_elapsed_s"] = round(stage_elapsed_s, 1)
        d.pop("stage_started_at", None)  # internal field
        return d

    def update(self, **kwargs):
        with self._lock:
            self._state = dataclasses.replace(self._state, **kwargs)

    @property
    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def _stage_cb(self, stage: str, total: int):
        self.update(
            stage=stage,
            total=total,
            progress=0,
            stage_started_at=datetime.now().isoformat(),
        )

    def _progress_cb(self, done: int):
        self.update(progress=done)


_scheduler = PipelineScheduler()


class RunLevelRequest(BaseModel):
    level: int  # 0, 1, or 2
    source_id: int | None = None  # required for level 0


@router.get("/status")
def get_status():
    return _scheduler.get_status()


@router.post("/run")
def run_level(req: RunLevelRequest, db: Database = Depends(get_db)):
    if req.level not in (0, 1, 2, 3):
        raise HTTPException(status_code=400, detail="Level must be 0, 1, 2, or 3")

    if req.level == 0 and req.source_id is None:
        raise HTTPException(status_code=400, detail="source_id required for level 0")

    config = load_config()

    def _run():
        try:
            _execute_level(req.level, req.source_id, db, config)
        except Exception as e:
            logger.error("Pipeline L%d failed: %s", req.level, e, exc_info=True)
            _scheduler.update(error=str(e))
        finally:
            _scheduler.update(running=False)
            if _scheduler._state.stage != "done":
                _scheduler.update(stage="idle")

    _scheduler.start(_run, level=req.level, source_id=req.source_id)

    return {"ok": True, "level": req.level}


@router.post("/stop")
def stop_pipeline():
    with _scheduler._lock:
        if not _scheduler._state.running:
            return {"ok": True, "message": "Not running"}
        _scheduler._state = dataclasses.replace(_scheduler._state, stage="stopping")
        _scheduler._stop_event.set()
    return {"ok": True}


class RunMarkedRequest(BaseModel):
    photo_ids: list[int]
    target_level: int = 3  # 2 = stop at CLIP, 3 = include faces


@router.post("/run-marked")
def run_marked(req: RunMarkedRequest, db: Database = Depends(get_db)):
    if not req.photo_ids:
        raise HTTPException(status_code=400, detail="photo_ids is empty")

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
                progress_callback=_scheduler._progress_cb,
                stage_callback=_scheduler._stage_cb,
            )

            # L3: face analysis (if requested)
            if req.target_level >= 3:
                placeholders = ",".join("?" * len(active_ids))
                l2_rows = db.conn.execute(
                    f"SELECT id FROM photos WHERE id IN ({placeholders}) AND processing_level >= 2",
                    active_ids,
                ).fetchall()
                l2_ids = [r["id"] for r in l2_rows]
                if l2_ids:
                    from photogal.pipeline.face_analyzer import FaceAnalyzer
                    fa = FaceAnalyzer()
                    fa.detect_faces(db, l2_ids,
                                    progress_callback=_scheduler._progress_cb,
                                    stage_callback=_scheduler._stage_cb)
                    db.update_photos_batch(
                        ["processing_level"],
                        [(3, pid) for pid in l2_ids],
                    )
                    db.commit()
                    fa.cluster_faces(db,
                                     progress_callback=_scheduler._progress_cb,
                                     stage_callback=_scheduler._stage_cb)

            _scheduler.update(stage="done", progress=_scheduler._state.total)

        except Exception as e:
            logger.error("Pipeline run-marked failed: %s", e, exc_info=True)
            _scheduler.update(error=str(e))
        finally:
            _scheduler.update(running=False)
            if _scheduler._state.stage != "done":
                _scheduler.update(stage="idle")

    _scheduler.start(_run, level=req.target_level, total=len(req.photo_ids))
    return {"ok": True}


def _execute_level(level: int, source_id: int | None, db: Database, config):
    if level == 0:
        from photogal.pipeline.scanner import Scanner, discover_files
        source = db.get_source(source_id)
        if not source:
            raise ValueError(f"Source {source_id} not found")
        scan_path = Path(source["path"])

        files = discover_files(scan_path, config.supported_extensions)
        _scheduler.update(
            total=len(files),
            stage="scanning",
            stage_started_at=datetime.now().isoformat(),
        )

        scanner = Scanner(config, max_workers=config.max_workers)
        result = scanner.run(db, source_id, scan_path,
                             pre_discovered_files=files,
                             progress_callback=_scheduler._progress_cb)

        # ── Auto-chain: clustering + geocoding ──────────────────────────
        if _scheduler._state.stage == "stopping":
            return

        rows = db.conn.execute(
            "SELECT id FROM photos WHERE processing_level = 0 AND source_id = ?",
            (source_id,),
        ).fetchall()
        l1_ids = [r["id"] for r in rows]

        if not l1_ids:
            _scheduler.update(
                stage="done",
                progress=result["scanned"],
                total=result["scanned"],
            )
            return

        _scheduler.update(level=1, progress=0, total=len(l1_ids))

        l1_photos = db.get_photos_by_ids(l1_ids)

        from photogal.pipeline.analyzer import Analyzer
        analyzer = Analyzer(config)
        l1_result = analyzer._run_l1(
            db, l1_photos, scoped=True,
            progress_callback=_scheduler._progress_cb,
            stage_callback=_scheduler._stage_cb,
        )

        _scheduler.update(
            stage="done",
            progress=l1_result["processed"],
            total=l1_result["processed"],
        )

    elif level == 1:
        from photogal.pipeline.analyzer import Analyzer

        analyzer = Analyzer(config)
        result = analyzer.run(db,
                              progress_callback=_scheduler._progress_cb,
                              stage_callback=_scheduler._stage_cb)
        _scheduler.update(
            stage="done",
            progress=result["processed"],
            total=result["processed"],
        )

    elif level == 2:
        from photogal.pipeline.analyzer import Analyzer

        analyzer = Analyzer(config)
        result = analyzer.run_clip(
            db,
            progress_callback=_scheduler._progress_cb,
            stage_callback=_scheduler._stage_cb,
        )
        _scheduler.update(
            stage="done",
            progress=result["processed"],
            total=result["processed"],
        )

    elif level == 3:
        from photogal.pipeline.face_analyzer import FaceAnalyzer

        analyzer = FaceAnalyzer()
        result = analyzer.run(
            db,
            progress_callback=_scheduler._progress_cb,
            stage_callback=_scheduler._stage_cb,
        )
        _scheduler.update(
            stage="done",
            progress=result["processed"],
            total=result["processed"],
        )


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
