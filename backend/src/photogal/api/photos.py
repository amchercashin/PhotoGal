"""Photos API: browse, thumbnail, metadata, decisions, file actions."""

import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from photogal.api.deps import get_db
from photogal.config import get_thumbnail_cache_dir
from photogal.db import Database, resolve_photo_path
from photogal.thumbnails import generate_thumbnail

router = APIRouter(prefix="/photos", tags=["photos"])

_sources_cache: list[Path] | None = None
_sources_cache_time: float = 0


def _get_source_paths(db: Database) -> list[Path]:
    global _sources_cache, _sources_cache_time
    now = time.monotonic()
    if _sources_cache is None or now - _sources_cache_time > 30:  # 30s TTL
        sources = db.get_all_sources()
        _sources_cache = [Path(s["path"]).resolve() for s in sources]
        _sources_cache_time = now
    return _sources_cache


def _validate_path_within_sources(filepath: str, db: Database):
    """Check that the resolved file path is within a registered source directory."""
    resolved = Path(filepath).resolve()
    for src_dir in _get_source_paths(db):
        try:
            resolved.relative_to(src_dir)
            return  # path is within this source
        except ValueError:
            continue
    raise HTTPException(status_code=403, detail="File path is outside registered sources")


@router.get("/")
def list_photos(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("exif_date"),
    sort_dir: str = Query("ASC"),
    filter_level: int | None = Query(None),
    filter_category: str | None = Query(None),
    filter_decision: str | None = Query(None),
    filter_cluster_id: int | None = Query(None),
    db: Database = Depends(get_db),
):
    photos = db.get_photos_paginated(
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir,
        filter_level=filter_level,
        filter_category=filter_category,
        filter_decision=filter_decision,
        filter_cluster_id=filter_cluster_id,
    )
    total = db.count_photos_filtered(
        filter_level=filter_level,
        filter_category=filter_category,
        filter_decision=filter_decision,
        filter_cluster_id=filter_cluster_id,
    )
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [_photo_row(p) for p in photos],
    }


@router.get("/stats")
def get_stats(db: Database = Depends(get_db)):
    return db.get_stats()


@router.get("/ids-by-level/{level}")
def get_ids_by_level(level: int, db: Database = Depends(get_db)):
    rows = db.conn.execute(
        "SELECT id, cluster_id FROM photos WHERE processing_level = ?", (level,)
    ).fetchall()
    ids = [r["id"] for r in rows]
    cluster_ids = list({r["cluster_id"] for r in rows if r["cluster_id"] is not None})
    return {"ids": ids, "cluster_ids": cluster_ids}


@router.get("/ids-by-sync/{status}")
def get_ids_by_sync(status: str, db: Database = Depends(get_db)):
    if status not in ("ok", "disconnected"):
        raise HTTPException(status_code=400, detail="status must be ok or disconnected")
    rows = db.conn.execute(
        "SELECT id, cluster_id FROM photos WHERE sync_status = ?", (status,)
    ).fetchall()
    ids = [r["id"] for r in rows]
    cluster_ids = list({r["cluster_id"] for r in rows if r["cluster_id"] is not None})
    return {"ids": ids, "cluster_ids": cluster_ids}


class PhotoIdsRequest(BaseModel):
    photo_ids: list[int]


@router.post("/level-info")
def get_level_info(req: PhotoIdsRequest, db: Database = Depends(get_db)):
    """Return min processing_level and disconnected count for a list of photo IDs."""
    if not req.photo_ids:
        return {"min_level": 0, "disconnected_count": 0, "active_count": 0}
    placeholders = ",".join("?" * len(req.photo_ids))
    rows = db.conn.execute(
        f"SELECT processing_level, sync_status FROM photos WHERE id IN ({placeholders})",
        req.photo_ids,
    ).fetchall()
    if not rows:
        return {"min_level": 0, "disconnected_count": 0, "active_count": 0}
    disconnected = sum(1 for r in rows if (r["sync_status"] or "ok") == "disconnected")
    active = [r for r in rows if (r["sync_status"] or "ok") != "disconnected"]
    min_level = min((r["processing_level"] for r in active), default=0)
    level_counts: dict[int, int] = {0: 0, 1: 0, 2: 0, 3: 0}
    for r in active:
        lvl = min(r["processing_level"], 3)
        level_counts[lvl] = level_counts.get(lvl, 0) + 1
    return {
        "min_level": min_level,
        "disconnected_count": disconnected,
        "active_count": len(active),
        "level_counts": level_counts,
    }


class BulkDeleteRequest(BaseModel):
    photo_ids: list[int]


@router.post("/bulk-delete")
def bulk_delete_photos(req: BulkDeleteRequest, db: Database = Depends(get_db)):
    if not req.photo_ids:
        return {"deleted": 0}
    db.delete_photos_bulk(req.photo_ids)
    db.cleanup_orphan_clusters()
    db.cleanup_orphaned_persons()
    db.commit()
    return {"deleted": len(req.photo_ids)}


@router.get("/{photo_id}/table-position")
def get_photo_table_position(
    photo_id: int,
    sort_by: str = Query("exif_date"),
    sort_dir: str = Query("ASC"),
    filter_category: str | None = Query(None),
    page_size: int = Query(100, ge=1, le=1000),
    db: Database = Depends(get_db),
):
    """Return the page number where photo_id appears given sort/filter params."""
    return db.get_photo_table_position(
        photo_id=photo_id,
        sort_by=sort_by,
        sort_dir=sort_dir,
        filter_category=filter_category,
        page_size=page_size,
    )


@router.get("/{photo_id}")
def get_photo(photo_id: int, db: Database = Depends(get_db)):
    photo = db.get_photo(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    return _photo_row(photo)


@router.get("/{photo_id}/thumbnail")
def get_thumbnail(photo_id: int, request: Request, db: Database = Depends(get_db)):
    photo = db.get_photo(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    filepath = resolve_photo_path(photo)
    _validate_path_within_sources(filepath, db)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found on disk")
    cache_dir = get_thumbnail_cache_dir()
    try:
        content_hash = photo["content_hash"] if "content_hash" in photo.keys() else None
        thumb = generate_thumbnail(filepath, cache_dir, content_hash=content_hash)
        stat = thumb.stat()
        etag = f'"{stat.st_mtime_ns}-{stat.st_size}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304)
        return FileResponse(
            str(thumb),
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-cache",
                "ETag": etag,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Thumbnail error: {e}")


@router.get("/{photo_id}/full")
def get_full_image(photo_id: int, db: Database = Depends(get_db)):
    from io import BytesIO
    from fastapi.responses import Response
    from PIL import Image, ImageOps
    import pillow_heif
    pillow_heif.register_heif_opener()

    photo = db.get_photo(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    filepath = resolve_photo_path(photo)
    _validate_path_within_sources(filepath, db)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found on disk")
    suffix = Path(filepath).suffix.lower()
    # HEIC/HEIF: convert to JPEG for universal browser compatibility
    if suffix in (".heic", ".heif"):
        try:
            img = Image.open(filepath)
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=92)
            buf.seek(0)
            return Response(content=buf.read(), media_type="image/jpeg")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Image conversion error: {e}")
    media_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    return FileResponse(filepath, media_type=media_type)




def _photo_row(p) -> dict:
    return {
        "id": p["id"],
        "content_hash": p["content_hash"] if "content_hash" in p.keys() else None,
        "source_id": p["source_id"],
        "original_path": p["original_path"],
        "original_filename": p["original_filename"],
        "current_path": p["current_path"],
        "file_size": p["file_size"],
        "processing_level": p["processing_level"],
        "cluster_id": p["cluster_id"],
        "exif_date": p["exif_date"],
        "exif_gps_lat": p["exif_gps_lat"],
        "exif_gps_lon": p["exif_gps_lon"],
        "exif_camera": p["exif_camera"],
        "exif_orientation": p["exif_orientation"],
        "exif_width": p["exif_width"],
        "exif_height": p["exif_height"],
        "location_country": p["location_country"],
        "location_city": p["location_city"],
        "location_district": p["location_district"],
        "quality_blur": p["quality_blur"],
        "quality_exposure": p["quality_exposure"],
        "quality_aesthetic": p["quality_aesthetic"],
        "face_count": p["face_count"],
        "is_technical": p["is_technical"],
        "semantic_tags": p["semantic_tags"],
        "content_category": p["content_category"],
        "rank_in_cluster": p["rank_in_cluster"],
        "user_decision": p["user_decision"],
        "sync_status": p["sync_status"] if "sync_status" in p.keys() else "ok",
        "is_exact_duplicate": p["is_exact_duplicate"] if "is_exact_duplicate" in p.keys() else 0,
        "created_at": p["created_at"],
        "updated_at": p["updated_at"],
    }


