"""FastAPI application factory."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from photogal.api.deps import set_db
from photogal.db import Database

logger = logging.getLogger(__name__)


def _warmup(db: Database) -> None:
    """Background warmup: pre-load embedding cache and argos-translate."""
    try:
        if db.count_embeddings() > 0:
            from photogal.search import _ensure_cache
            _ensure_cache(db)
            logger.info("Warmup: embedding cache loaded")
        from photogal.translate import is_installed
        if is_installed():
            from photogal.translate import _get_translator
            _get_translator()
            logger.info("Warmup: argos-translate loaded")
    except Exception:
        logger.warning("Warmup failed (non-fatal)", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from photogal.api.deps import get_db
    db = get_db()
    db.cleanup_orphaned_persons()
    threading.Thread(target=_warmup, args=(db,), daemon=True).start()
    yield


def create_app(db_path: str | Path | None = None) -> FastAPI:
    from photogal.config import get_db_path
    if db_path is None:
        db_path = get_db_path()

    db = Database(db_path)
    set_db(db)

    app = FastAPI(
        title="PhotoGal",
        description="Progressive photo library organizer",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Local-only server: allow all origins.
    # Tauri WKWebView on macOS may send Origin: null (opaque origin from
    # custom tauri:// scheme), which doesn't match named origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from photogal.api.sources import router as sources_router
    from photogal.api.photos import router as photos_router
    from photogal.api.clusters import router as clusters_router
    from photogal.api.process import router as process_router
    from photogal.api.sync import router as sync_router
    from photogal.api.search import router as search_router
    from photogal.api.persons import router as persons_router
    from photogal.api.faces import router as faces_router
    from photogal.api.device import router as device_router

    app.include_router(sources_router, prefix="/api")
    app.include_router(photos_router, prefix="/api")
    app.include_router(clusters_router, prefix="/api")
    app.include_router(process_router, prefix="/api")
    app.include_router(sync_router, prefix="/api")
    app.include_router(search_router, prefix="/api")
    app.include_router(persons_router, prefix="/api")
    app.include_router(faces_router, prefix="/api")
    app.include_router(device_router, prefix="/api")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    # Serve built frontend if present (for production)
    frontend_dist = Path(__file__).parent.parent.parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")

    return app
