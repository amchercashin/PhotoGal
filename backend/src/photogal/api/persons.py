"""Persons API: list, rename, hide, get photos."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from photogal.api.deps import get_db
from photogal.db import Database

router = APIRouter(prefix="/persons", tags=["persons"])


@router.get("/")
def list_persons(
    include_hidden: bool = False,
    limit: int = 100,
    offset: int = 0,
    db: Database = Depends(get_db),
):
    persons = db.list_persons(include_hidden=include_hidden, limit=limit, offset=offset)
    return [_person_row(p) for p in persons]


@router.get("/{person_id}/photos")
def get_person_photos(person_id: int, db: Database = Depends(get_db)):
    photo_ids = db.get_person_photo_ids(person_id)
    return {"photo_ids": photo_ids, "total": len(photo_ids)}


class PersonUpdateRequest(BaseModel):
    name: str | None = None
    hidden: bool | None = None


@router.patch("/{person_id}")
def update_person(
    person_id: int,
    req: PersonUpdateRequest,
    db: Database = Depends(get_db),
):
    row = db.conn.execute("SELECT id FROM persons WHERE id = ?", (person_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")
    if req.name is not None:
        db.rename_person(person_id, req.name)
    if req.hidden is not None:
        db.hide_person(person_id, req.hidden)
    return {"ok": True}


def _person_row(p) -> dict:
    return {
        "id": p["id"],
        "name": p["name"],
        "face_count": p["face_count"],
        "representative_face_id": p["representative_face_id"],
        "hidden": bool(p["hidden"]),
    }
