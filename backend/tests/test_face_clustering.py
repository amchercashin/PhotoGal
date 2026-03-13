"""Tests for face clustering logic (Union-Find + cosine similarity)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
from photogal.db import Database


def _make_db() -> Database:
    return Database(":memory:")


def _insert_face_with_embedding(db, photo_id, embedding, bbox_x=0.3):
    cur = db.conn.execute(
        "INSERT INTO faces (photo_id, bbox_x, bbox_y, bbox_w, bbox_h, confidence, source_size) "
        "VALUES (?, ?, 0.1, 0.2, 0.35, 0.95, 'thumbnail')",
        (photo_id, bbox_x),
    )
    face_id = cur.lastrowid
    db.conn.execute(
        "INSERT INTO face_embeddings (face_id, embedding) VALUES (?, ?)",
        (face_id, embedding.tobytes()),
    )
    return face_id


def test_identical_embeddings_merge():
    """Faces with identical embeddings should cluster together."""
    db = _make_db()
    db.conn.execute("INSERT INTO sources (path, name) VALUES ('/tmp', 'test')")
    for i in range(3):
        db.conn.execute(
            "INSERT INTO photos (source_id, content_hash, original_path, original_filename, "
            "processing_level, exif_width, exif_height) VALUES (1, ?, ?, ?, 2, 4000, 3000)",
            (f"h{i}", f"/tmp/{i}.jpg", f"{i}.jpg"),
        )

    emb = np.random.randn(512).astype(np.float32)
    emb = emb / np.linalg.norm(emb)  # L2-normalize
    for pid in [1, 2, 3]:
        _insert_face_with_embedding(db, pid, emb)
    db.commit()

    from photogal.pipeline.face_analyzer import FaceAnalyzer
    analyzer = FaceAnalyzer()
    result = analyzer.cluster_faces(db, similarity_threshold=0.5)

    assert result["persons_created"] == 1
    persons = db.list_persons()
    assert persons[0]["face_count"] == 3
    db.close()


def test_different_embeddings_separate():
    """Orthogonal embeddings should NOT cluster together."""
    db = _make_db()
    db.conn.execute("INSERT INTO sources (path, name) VALUES ('/tmp', 'test')")
    for i in range(3):
        db.conn.execute(
            "INSERT INTO photos (source_id, content_hash, original_path, original_filename, "
            "processing_level, exif_width, exif_height) VALUES (1, ?, ?, ?, 2, 4000, 3000)",
            (f"h{i}", f"/tmp/{i}.jpg", f"{i}.jpg"),
        )

    # Create orthogonal embeddings
    for pid in range(1, 4):
        emb = np.zeros(512, dtype=np.float32)
        emb[pid * 100] = 1.0  # different dimension for each
        _insert_face_with_embedding(db, pid, emb)
    db.commit()

    from photogal.pipeline.face_analyzer import FaceAnalyzer
    analyzer = FaceAnalyzer()
    result = analyzer.cluster_faces(db, similarity_threshold=0.5)

    assert result["persons_created"] == 3
    db.close()


def test_transitive_merge():
    """A~B and B~C should result in A, B, C in same cluster (transitivity)."""
    db = _make_db()
    db.conn.execute("INSERT INTO sources (path, name) VALUES ('/tmp', 'test')")
    for i in range(3):
        db.conn.execute(
            "INSERT INTO photos (source_id, content_hash, original_path, original_filename, "
            "processing_level, exif_width, exif_height) VALUES (1, ?, ?, ?, 2, 4000, 3000)",
            (f"h{i}", f"/tmp/{i}.jpg", f"{i}.jpg"),
        )

    base = np.random.randn(512).astype(np.float32)
    base = base / np.linalg.norm(base)
    noise1 = np.random.randn(512).astype(np.float32) * 0.05
    noise2 = np.random.randn(512).astype(np.float32) * 0.05

    emb_a = base.copy()
    emb_b = base + noise1
    emb_b = emb_b / np.linalg.norm(emb_b)
    emb_c = base + noise2
    emb_c = emb_c / np.linalg.norm(emb_c)

    _insert_face_with_embedding(db, 1, emb_a)
    _insert_face_with_embedding(db, 2, emb_b)
    _insert_face_with_embedding(db, 3, emb_c)
    db.commit()

    from photogal.pipeline.face_analyzer import FaceAnalyzer
    analyzer = FaceAnalyzer()
    result = analyzer.cluster_faces(db, similarity_threshold=0.5)

    # All 3 should merge (cosine sim ~0.99 for small noise)
    assert result["persons_created"] == 1
    db.close()


def test_centroid_stored():
    """After clustering, persons should have centroid BLOB."""
    db = _make_db()
    db.conn.execute("INSERT INTO sources (path, name) VALUES ('/tmp', 'test')")
    db.conn.execute(
        "INSERT INTO photos (source_id, content_hash, original_path, original_filename, "
        "processing_level, exif_width, exif_height) VALUES (1, 'h', '/tmp/0.jpg', '0.jpg', 2, 4000, 3000)"
    )
    emb = np.random.randn(512).astype(np.float32)
    emb = emb / np.linalg.norm(emb)
    _insert_face_with_embedding(db, 1, emb)
    db.commit()

    from photogal.pipeline.face_analyzer import FaceAnalyzer
    FaceAnalyzer().cluster_faces(db)

    row = db.conn.execute("SELECT centroid FROM persons WHERE id = 1").fetchone()
    assert row["centroid"] is not None
    centroid = np.frombuffer(row["centroid"], dtype=np.float32)
    assert centroid.shape == (512,)
    db.close()
