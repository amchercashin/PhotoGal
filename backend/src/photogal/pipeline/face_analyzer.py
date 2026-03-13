"""Face analysis pipeline: detection, embedding, clustering (L3)."""

import logging
import time

import numpy as np
from PIL import Image

from photogal.api.deps import get_face_model
from photogal.config import get_thumbnail_cache_dir
from photogal.db import Database, _chunks

log = logging.getLogger(__name__)

HYBRID_THRESHOLD_PX = 80  # Re-detect on original if face < this on thumbnail
THUMBNAIL_SIZE = 400


def _load_image(photo: dict, use_thumbnail: bool = True) -> tuple[np.ndarray, int, int] | None:
    """Load image as BGR numpy array. Returns (img, orig_w, orig_h) or None."""
    import cv2

    orig_w = photo["exif_width"] or 4000
    orig_h = photo["exif_height"] or 3000

    if use_thumbnail:
        thumb_dir = get_thumbnail_cache_dir()
        from photogal.thumbnails import get_thumbnail_path
        thumb_path = get_thumbnail_path(thumb_dir, content_hash=photo["content_hash"])
        if thumb_path.exists():
            img = cv2.imread(str(thumb_path))
            if img is not None:
                return img, orig_w, orig_h

    # Fallback to original
    from photogal.db import resolve_photo_path
    path = resolve_photo_path(photo)
    if not path:
        return None
    img = cv2.imread(str(path))
    if img is None:
        # Try HEIC via Pillow
        try:
            pil_img = Image.open(str(path))
            pil_img = pil_img.convert("RGB")
            img = np.array(pil_img)[:, :, ::-1]  # RGB->BGR
        except Exception:
            return None
    return img, orig_w, orig_h


class FaceAnalyzer:
    """L3 face analysis: detection + embedding + clustering."""

    def detect_faces(
        self,
        db: Database,
        photo_ids: list[int],
        progress_callback=None,
        stage_callback=None,
    ) -> dict:
        """Phase 7: Detect faces + extract embeddings for given photos."""
        if stage_callback:
            stage_callback("faces", len(photo_ids))

        model = get_face_model()
        processed = 0
        errors = 0
        t0 = time.time()

        for i, pid in enumerate(photo_ids):
            photo = db.conn.execute("SELECT * FROM photos WHERE id = ?", (pid,)).fetchone()
            if not photo:
                errors += 1
                continue

            result = _load_image(photo, use_thumbnail=True)
            if result is None:
                db.update_photo(pid, face_count=0)
                errors += 1
                continue

            img, orig_w, orig_h = result
            thumb_h, thumb_w = img.shape[:2]
            detected = model.detect(img)

            # Hybrid: re-detect on original for small faces
            needs_original = False
            for f in detected:
                face_w_px = f["bbox_w"] * thumb_w
                if face_w_px < HYBRID_THRESHOLD_PX:
                    needs_original = True
                    break

            if needs_original and thumb_w < orig_w:
                orig_result = _load_image(photo, use_thumbnail=False)
                if orig_result is not None:
                    img, orig_w, orig_h = orig_result
                    detected = model.detect(img)

            # Store faces
            if detected:
                faces_data = []
                embeddings = []
                for f in detected:
                    faces_data.append({
                        "photo_id": pid,
                        "bbox_x": f["bbox_x"],
                        "bbox_y": f["bbox_y"],
                        "bbox_w": f["bbox_w"],
                        "bbox_h": f["bbox_h"],
                        "confidence": f["confidence"],
                        "source_size": "original" if needs_original else "thumbnail",
                    })
                    embeddings.append(f["embedding"])
                db.insert_faces_batch(faces_data, embeddings)

            db.update_photo(pid, face_count=len(detected))
            processed += 1

            if progress_callback:
                progress_callback(i + 1)

        db.commit()
        elapsed = time.time() - t0
        log.info("Phase 7: detected faces in %d photos (%.1fs, %d errors)",
                 processed, elapsed, errors)
        return {"processed": processed, "errors": errors}

    def cluster_faces(
        self,
        db: Database,
        similarity_threshold: float = 0.5,
        k_neighbors: int = 20,
        progress_callback=None,
        stage_callback=None,
    ) -> dict:
        """Phase 8: Cluster faces into persons using ANN + Union-Find."""
        if stage_callback:
            stage_callback("face-clustering", 0)

        face_ids, matrix = db.get_all_face_embeddings()
        n = len(face_ids)
        if n == 0:
            return {"persons_created": 0}

        log.info("Phase 8: clustering %d faces...", n)
        t0 = time.time()

        # Batched cosine similarity + Union-Find
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int):
            pa, pb = find(a), find(b)
            if pa != pb:
                parent[pa] = pb

        # Normalize for cosine (should already be L2-normalized, but ensure)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix = matrix / norms

        # Batched similarity: process 1000 rows at a time
        batch_size = 1000
        k = min(k_neighbors, n - 1)
        for start in range(0, n, batch_size):
            end = min(start + batch_size, n)
            sims = matrix[start:end] @ matrix.T  # (batch, N)
            for local_i in range(end - start):
                global_i = start + local_i
                row = sims[local_i]
                # Get top-k (excluding self)
                if k > 0:
                    top_k_idx = np.argpartition(row, -(k + 1))[-(k + 1):]
                    for j in top_k_idx:
                        if j != global_i and row[j] > similarity_threshold:
                            union(global_i, j)

            if progress_callback:
                progress_callback(end)

        # Collect groups
        groups: dict[int, list[int]] = {}
        for i in range(n):
            root = find(i)
            groups.setdefault(root, []).append(i)

        # Save old person name → representative_face_id mapping before clearing
        old_persons = db.conn.execute(
            "SELECT id, name, representative_face_id FROM persons WHERE name IS NOT NULL"
        ).fetchall()
        old_name_by_rep_face: dict[int, str] = {
            r["representative_face_id"]: r["name"]
            for r in old_persons if r["representative_face_id"] is not None
        }

        # Clear old persons and reset face assignments
        db.conn.execute("DELETE FROM persons")
        db.conn.execute("UPDATE faces SET person_id = NULL")

        # Bulk-fetch face metadata for representative selection (Issue 6 fix)
        all_face_ids_flat = list(face_ids)
        face_meta: dict[int, dict] = {}
        for chunk in _chunks(all_face_ids_flat, 900):
            ph = ",".join("?" * len(chunk))
            rows = db.conn.execute(
                f"SELECT id, confidence, bbox_w, bbox_h FROM faces WHERE id IN ({ph})",
                chunk,
            ).fetchall()
            for r in rows:
                face_meta[r["id"]] = {"confidence": r["confidence"],
                                      "bbox_w": r["bbox_w"], "bbox_h": r["bbox_h"]}

        # Create persons
        persons_created = 0
        for member_indices in groups.values():
            member_face_ids = [face_ids[i] for i in member_indices]
            member_embeddings = matrix[member_indices]
            centroid = member_embeddings.mean(axis=0)
            centroid = centroid / (np.linalg.norm(centroid) + 1e-8)

            # Pick representative: highest confidence x largest bbox
            best_face_id = member_face_ids[0]
            best_score = 0.0
            for fid in member_face_ids:
                fm = face_meta.get(fid)
                if fm:
                    score = fm["confidence"] * (fm["bbox_w"] * fm["bbox_h"])
                    if score > best_score:
                        best_score = score
                        best_face_id = fid

            # Restore name if the representative face belonged to a named person
            restored_name = old_name_by_rep_face.get(best_face_id)
            # Also check other member faces for name match
            if not restored_name:
                for fid in member_face_ids:
                    restored_name = old_name_by_rep_face.get(fid)
                    if restored_name:
                        break

            person_id = db.create_person(
                face_count=len(member_face_ids),
                representative_face_id=best_face_id,
                centroid=centroid,
                name=restored_name,
            )
            db.assign_faces_to_person(member_face_ids, person_id)
            persons_created += 1

        db.commit()
        elapsed = time.time() - t0
        log.info("Phase 8: created %d persons from %d faces (%.1fs)",
                 persons_created, n, elapsed)
        return {"persons_created": persons_created}

    def run(
        self,
        db: Database,
        progress_callback=None,
        stage_callback=None,
    ) -> dict:
        """Run full L3: Phase 7 (detect) + Phase 8 (cluster)."""
        # Get all photos at level >= 2 that haven't been face-analyzed yet
        rows = db.conn.execute(
            "SELECT id FROM photos WHERE processing_level = 2"
        ).fetchall()
        photo_ids = [r["id"] for r in rows]

        if not photo_ids:
            return {"processed": 0, "persons_created": 0}

        r1 = self.detect_faces(db, photo_ids,
                               progress_callback=progress_callback,
                               stage_callback=stage_callback)

        # Update processing_level to 3
        db.update_photos_batch(["processing_level"], [(3, pid) for pid in photo_ids])
        db.commit()

        r2 = self.cluster_faces(db,
                                progress_callback=progress_callback,
                                stage_callback=stage_callback)

        return {**r1, **r2}
