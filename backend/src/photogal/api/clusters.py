"""Clusters API."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from photogal.api.deps import get_db
from photogal.api.photos import _photo_row
from photogal.db import Database

router = APIRouter(prefix="/clusters", tags=["clusters"])


@router.get("/")
def list_clusters(
    nonempty: bool = Query(True),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: Database = Depends(get_db),
):
    clusters, total = db.get_clusters_paginated(
        limit=limit, offset=offset, nonempty=nonempty,
    )
    return {
        "items": [_cluster_row_light(c) for c in clusters],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


class PhotoIdsRequest(BaseModel):
    cluster_ids: list[int]


@router.post("/photo-ids")
def get_cluster_photo_ids(req: PhotoIdsRequest, db: Database = Depends(get_db)):
    result = db.get_photo_ids_by_cluster_ids(req.cluster_ids)
    return result


@router.get("/{cluster_id}")
def get_cluster(cluster_id: int, db: Database = Depends(get_db)):
    cluster = db.get_cluster_by_id(cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    photos = db.get_photos_by_cluster(cluster_id)
    photo_ids = [p["id"] for p in photos]
    return {
        **_cluster_row_light(cluster),
        "photo_ids": photo_ids,
        "photos": [_photo_row(p) for p in photos],
    }


class MovePhotoRequest(BaseModel):
    photo_id: int
    target_cluster_id: int


@router.post("/move")
def move_photo_to_cluster(req: MovePhotoRequest, db: Database = Depends(get_db)):
    db.move_photo_to_cluster(req.photo_id, req.target_cluster_id)
    db.commit()
    return {"ok": True}


def _cluster_row_light(c) -> dict:
    """Lightweight cluster serialization — no photo_ids (fetched separately)."""
    def _safe(key, default=None):
        try:
            return c[key]
        except (IndexError, KeyError):
            return default

    return {
        "id": c["id"],
        "name": c["name"],
        "best_photo_id": c["best_photo_id"],
        "photo_count": c["photo_count"],
        "type": c["type"],
        "avg_timestamp": c["avg_timestamp"],
        "avg_gps_lat": c["avg_gps_lat"],
        "avg_gps_lon": c["avg_gps_lon"],
        "location_city": c["location_city"],
        "event_id": c["event_id"],
        "best_photo_blur": _safe("best_photo_blur"),
        "best_photo_exposure": _safe("best_photo_exposure"),
        "has_exact_duplicate": bool(_safe("has_exact_duplicate", 0)),
    }
