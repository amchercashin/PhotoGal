"""Faces API: per-photo face list, face thumbnails."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from photogal.api.deps import get_db
from photogal.config import get_thumbnail_cache_dir
from photogal.db import Database

router = APIRouter(prefix="/faces", tags=["faces"])


@router.get("/photo/{photo_id}")
def get_faces_for_photo(photo_id: int, db: Database = Depends(get_db)):
    """Get all faces detected on a photo (for Viewer overlay)."""
    faces = db.get_faces_by_photo(photo_id)
    return faces


@router.get("/{face_id}/thumb")
def get_face_thumbnail(face_id: int, db: Database = Depends(get_db)):
    """150x150 JPEG crop of the face. Cached on first request."""
    cache_dir = get_thumbnail_cache_dir() / "faces"
    cache_path = cache_dir / f"{face_id}.jpg"

    if cache_path.exists():
        return Response(content=cache_path.read_bytes(), media_type="image/jpeg")

    # Generate face thumbnail
    face = db.conn.execute(
        "SELECT f.*, p.id as photo_db_id, p.exif_width, p.exif_height, p.content_hash "
        "FROM faces f JOIN photos p ON f.photo_id = p.id WHERE f.id = ?",
        (face_id,),
    ).fetchone()
    if not face:
        raise HTTPException(status_code=404, detail="Face not found")

    # Load photo thumbnail and crop face region
    from photogal.thumbnails import get_thumbnail_path
    thumb_dir = get_thumbnail_cache_dir()
    thumb_path = get_thumbnail_path(thumb_dir, content_hash=face["content_hash"])
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Photo thumbnail not found")

    from PIL import Image
    img = Image.open(thumb_path)
    w, h = img.size

    # bbox is normalized; scale to thumbnail dims
    x = int(face["bbox_x"] * w)
    y = int(face["bbox_y"] * h)
    bw = int(face["bbox_w"] * w)
    bh = int(face["bbox_h"] * h)

    # Expand to square crop with padding
    cx, cy = x + bw // 2, y + bh // 2
    side = max(bw, bh)
    pad = int(side * 0.2)
    side += pad * 2
    x1 = max(0, cx - side // 2)
    y1 = max(0, cy - side // 2)
    x2 = min(w, x1 + side)
    y2 = min(h, y1 + side)

    crop = img.crop((x1, y1, x2, y2)).resize((150, 150))

    cache_dir.mkdir(parents=True, exist_ok=True)
    crop.save(cache_path, "JPEG", quality=85)
    return Response(content=cache_path.read_bytes(), media_type="image/jpeg")
