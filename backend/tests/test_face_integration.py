"""Integration test: L3 face pipeline end-to-end (with mocked model)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
from unittest.mock import MagicMock, patch
from photogal.db import Database


def _make_db():
    return Database(":memory:")


def test_full_l3_pipeline():
    """Full L3: detect faces → cluster → verify persons created."""
    db = _make_db()
    db.conn.execute("INSERT INTO sources (path, name) VALUES ('/tmp', 'test')")

    # 5 photos: 3 of person A, 2 of person B
    for i in range(5):
        db.conn.execute(
            "INSERT INTO photos (source_id, content_hash, original_path, original_filename, "
            "processing_level, exif_width, exif_height) VALUES (1, ?, ?, ?, 2, 4000, 3000)",
            (f"h{i}", f"/tmp/{i}.jpg", f"{i}.jpg"),
        )
    db.commit()

    # Create stable embeddings for 2 "people"
    rng = np.random.RandomState(42)
    person_a_base = rng.randn(512).astype(np.float32)
    person_a_base /= np.linalg.norm(person_a_base)
    person_b_base = rng.randn(512).astype(np.float32)
    person_b_base /= np.linalg.norm(person_b_base)

    # Assign: photos 1-3 get person A face, photos 4-5 get person B face
    call_count = [0]
    def mock_detect(img):
        call_count[0] += 1
        idx = call_count[0]
        if idx <= 3:
            base = person_a_base
        else:
            base = person_b_base
        noise = rng.randn(512).astype(np.float32) * 0.02
        emb = base + noise
        emb /= np.linalg.norm(emb)
        return [{
            "bbox_x": 0.3, "bbox_y": 0.1, "bbox_w": 0.2, "bbox_h": 0.35,
            "confidence": 0.95,
            "embedding": emb,
        }]

    with patch("photogal.pipeline.face_analyzer.get_face_model") as mock_get:
        mock_model = MagicMock()
        mock_model.detect.side_effect = mock_detect
        mock_get.return_value = mock_model

        with patch("photogal.pipeline.face_analyzer._load_image") as mock_load:
            mock_load.return_value = (np.zeros((400, 600, 3), dtype=np.uint8), 4000, 3000)

            from photogal.pipeline.face_analyzer import FaceAnalyzer
            analyzer = FaceAnalyzer()
            result = analyzer.run(db)

    assert result["processed"] == 5
    assert result["persons_created"] == 2

    persons = db.list_persons()
    counts = sorted([p["face_count"] for p in persons], reverse=True)
    assert counts == [3, 2]

    # All photos should be level 3
    for i in range(1, 6):
        row = db.conn.execute("SELECT processing_level FROM photos WHERE id = ?", (i,)).fetchone()
        assert row["processing_level"] == 3

    db.close()
