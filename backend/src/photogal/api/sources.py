"""Sources API: manage photo library source folders."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from photogal.api.deps import get_db
from photogal.db import Database

router = APIRouter(prefix="/sources", tags=["sources"])


class AddSourceRequest(BaseModel):
    path: str
    name: str | None = None


@router.get("/")
def list_sources(db: Database = Depends(get_db)):
    sources = db.get_all_sources()
    return [dict(s) for s in sources]


@router.post("/")
def add_source(req: AddSourceRequest, db: Database = Depends(get_db)):
    p = Path(req.path)
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Path does not exist or is not a directory: {req.path}")
    source_id = db.add_source(str(p.resolve()), req.name)
    source = db.get_source(source_id)
    return dict(source)


@router.delete("/{source_id}")
def remove_source(
    source_id: int,
    delete_photos: bool = Query(False),
    db: Database = Depends(get_db),
):
    source = db.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if delete_photos:
        photo_ids = db.get_photo_ids_by_source(source_id)
        if photo_ids:
            db.delete_photos_bulk(photo_ids)
            db.cleanup_orphan_clusters()
            db.cleanup_orphaned_persons()
    else:
        db.conn.execute("UPDATE photos SET source_id = NULL WHERE source_id = ?", (source_id,))
    db.conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    db.commit()
    return {"ok": True}
