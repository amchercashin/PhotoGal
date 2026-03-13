"""Tests for face analyzer pipeline."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
from unittest.mock import MagicMock, patch
from photogal.db import Database


def _make_db() -> Database:
    return Database(":memory:")


def _setup_photos(db: Database, n: int = 3) -> list[int]:
    """Insert source + n photos at level 2, return photo_ids."""
    db.conn.execute("INSERT INTO sources (path, name) VALUES ('/tmp/photos', 'test')")
    ids = []
    for i in range(n):
        cur = db.conn.execute(
            "INSERT INTO photos (source_id, content_hash, original_path, original_filename, "
            "processing_level, exif_width, exif_height) "
            "VALUES (1, ?, ?, ?, 2, 4000, 3000)",
            (f"hash{i}", f"/tmp/photos/p{i}.jpg", f"p{i}.jpg"),
        )
        ids.append(cur.lastrowid)
    db.commit()
    return ids


def test_detect_faces_phase(tmp_path):
    """Phase 7: detect faces and store in DB."""
    db = _make_db()
    photo_ids = _setup_photos(db, 2)

    mock_face = {
        "bbox_x": 0.3, "bbox_y": 0.1, "bbox_w": 0.2, "bbox_h": 0.35,
        "confidence": 0.95,
        "embedding": np.random.randn(512).astype(np.float32),
    }

    with patch("photogal.pipeline.face_analyzer.get_face_model") as mock_get:
        mock_model = MagicMock()
        mock_model.detect.return_value = [mock_face]
        mock_get.return_value = mock_model

        with patch("photogal.pipeline.face_analyzer._load_image") as mock_load:
            mock_load.return_value = (np.zeros((400, 600, 3), dtype=np.uint8), 400, 600)

            from photogal.pipeline.face_analyzer import FaceAnalyzer
            analyzer = FaceAnalyzer()
            result = analyzer.detect_faces(db, photo_ids)

    assert result["processed"] == 2
    # Each photo should have 1 face
    for pid in photo_ids:
        faces = db.get_faces_by_photo(pid)
        assert len(faces) == 1
    # face_count should be updated
    row = db.conn.execute("SELECT face_count FROM photos WHERE id = ?", (photo_ids[0],)).fetchone()
    assert row["face_count"] == 1
    db.close()


def test_detect_faces_no_faces():
    """Photos with no faces get face_count=0."""
    db = _make_db()
    photo_ids = _setup_photos(db, 1)

    with patch("photogal.pipeline.face_analyzer.get_face_model") as mock_get:
        mock_model = MagicMock()
        mock_model.detect.return_value = []
        mock_get.return_value = mock_model

        with patch("photogal.pipeline.face_analyzer._load_image") as mock_load:
            mock_load.return_value = (np.zeros((400, 600, 3), dtype=np.uint8), 400, 600)

            from photogal.pipeline.face_analyzer import FaceAnalyzer
            analyzer = FaceAnalyzer()
            analyzer.detect_faces(db, photo_ids)

    row = db.conn.execute("SELECT face_count FROM photos WHERE id = ?", (photo_ids[0],)).fetchone()
    assert row["face_count"] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0
    db.close()
