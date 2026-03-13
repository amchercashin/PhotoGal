"""Tests for CLIP-based cluster merging (_clip_merge_clusters)."""
import sqlite3
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
from photogal.db import Database
from photogal.pipeline.analyzer import Analyzer
from photogal.config import Config


def _make_db():
    """Create in-memory DB with schema."""
    db = Database(":memory:")
    return db


def _insert_photo(db, pid, cluster_id, exif_date=None, lat=None, lon=None,
                  processing_level=2, user_cluster_override=None):
    """Insert photo with cluster_id (cluster must already exist or be None)."""
    db.conn.execute(
        "INSERT INTO photos (id, content_hash, original_path, original_filename, "
        "processing_level, cluster_id, exif_date, exif_gps_lat, exif_gps_lon, "
        "user_cluster_override) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, f"hash_{pid}", f"/path/{pid}.jpg", f"photo_{pid}.jpg",
         processing_level, cluster_id, exif_date, lat, lon, user_cluster_override),
    )


def _setup_photo_cluster(db, pid, cid, ctype="content", **photo_kwargs):
    """Insert a cluster (best_photo_id=NULL) + photo, then link them."""
    # Create cluster without best_photo_id first (avoids FK on photos)
    db.conn.execute(
        "INSERT INTO clusters (id, name, photo_count, type) VALUES (?, ?, ?, ?)",
        (cid, f"cluster_{cid}", 1, ctype),
    )
    _insert_photo(db, pid, cluster_id=cid, **photo_kwargs)
    db.conn.execute("UPDATE clusters SET best_photo_id = ? WHERE id = ?", (pid, cid))


def _make_embedding(dim=768, seed=42):
    """Create a random L2-normalized embedding."""
    rng = np.random.RandomState(seed)
    v = rng.randn(dim).astype(np.float32)
    v /= np.linalg.norm(v)
    return v


def _similar_embedding(base, similarity=0.95, dim=768, seed=99):
    """Create an embedding with approximately `similarity` cosine to `base`."""
    rng = np.random.RandomState(seed)
    noise = rng.randn(dim).astype(np.float32)
    noise -= np.dot(noise, base) * base  # orthogonal component
    noise /= np.linalg.norm(noise)
    # base * cos(angle) + noise * sin(angle), cos(angle)=similarity
    angle = np.arccos(np.clip(similarity, -1, 1))
    v = base * np.cos(angle) + noise * np.sin(angle)
    v /= np.linalg.norm(v)
    return v.astype(np.float32)


def _run_clip_merge(db, photos_list, embeddings_dict):
    """Run _clip_merge_clusters via Analyzer instance."""
    config = Config()
    analyzer = Analyzer(config)
    return analyzer._clip_merge_clusters(db, photos_list, embeddings_dict)


# ─── Test cases ─────────────────────────────────────────────────────────────────


def test_clip_merge_high_similarity_close_time():
    """Two clusters with CLIP >= 0.90 and time <= 180s → merge."""
    db = _make_db()
    _setup_photo_cluster(db, 1, 1, exif_date="2024:01:01 12:00:00")
    _setup_photo_cluster(db, 2, 2, exif_date="2024:01:01 12:02:00")  # 120s

    emb1 = _make_embedding(seed=10)
    emb2 = _similar_embedding(emb1, similarity=0.95, seed=20)
    db.set_embedding(1, emb1.tobytes())
    db.set_embedding(2, emb2.tobytes())
    db.commit()

    n = _run_clip_merge(db, [], {})
    assert n == 1, f"Expected 1 merge, got {n}"

    r1 = db.conn.execute("SELECT cluster_id FROM photos WHERE id=1").fetchone()
    r2 = db.conn.execute("SELECT cluster_id FROM photos WHERE id=2").fetchone()
    assert r1["cluster_id"] == r2["cluster_id"], "Photos should be in the same cluster"
    assert r1["cluster_id"] == 1, "Keep cluster should be the lower id"

    c2 = db.conn.execute("SELECT * FROM clusters WHERE id=2").fetchone()
    assert c2 is None, "Absorbed cluster should be deleted"


def test_clip_merge_high_similarity_far_time():
    """CLIP >= 0.90 but time > 180s → NO merge."""
    db = _make_db()
    _setup_photo_cluster(db, 1, 1, exif_date="2024:01:01 12:00:00")
    _setup_photo_cluster(db, 2, 2, exif_date="2024:01:01 12:10:00")  # 600s

    emb1 = _make_embedding(seed=10)
    emb2 = _similar_embedding(emb1, similarity=0.95, seed=20)
    db.set_embedding(1, emb1.tobytes())
    db.set_embedding(2, emb2.tobytes())
    db.commit()

    n = _run_clip_merge(db, [], {})
    assert n == 0, f"Expected 0 merges (time > 180s), got {n}"


def test_clip_merge_low_similarity_close_time():
    """CLIP < 0.90 + time <= 180s → NO merge."""
    db = _make_db()
    _setup_photo_cluster(db, 1, 1, exif_date="2024:01:01 12:00:00")
    _setup_photo_cluster(db, 2, 2, exif_date="2024:01:01 12:01:00")  # 60s

    emb1 = _make_embedding(seed=10)
    emb2 = _similar_embedding(emb1, similarity=0.50, seed=20)
    db.set_embedding(1, emb1.tobytes())
    db.set_embedding(2, emb2.tobytes())
    db.commit()

    n = _run_clip_merge(db, [], {})
    assert n == 0, f"Expected 0 merges (CLIP < 0.90), got {n}"


def test_clip_merge_skip_dup_clusters():
    """Dup clusters are never merged."""
    db = _make_db()
    _setup_photo_cluster(db, 1, 1, ctype="dup", exif_date="2024:01:01 12:00:00")
    _setup_photo_cluster(db, 2, 2, ctype="dup", exif_date="2024:01:01 12:00:30")

    emb1 = _make_embedding(seed=10)
    emb2 = _similar_embedding(emb1, similarity=0.99, seed=20)
    db.set_embedding(1, emb1.tobytes())
    db.set_embedding(2, emb2.tobytes())
    db.commit()

    n = _run_clip_merge(db, [], {})
    assert n == 0, f"Expected 0 merges (dup clusters), got {n}"


def test_clip_merge_transitive_union_find():
    """Transitive merge: A~B and B~C → all in one cluster."""
    db = _make_db()
    _setup_photo_cluster(db, 1, 1, exif_date="2024:01:01 12:00:00")
    _setup_photo_cluster(db, 2, 2, exif_date="2024:01:01 12:00:30")
    _setup_photo_cluster(db, 3, 3, exif_date="2024:01:01 12:01:00")

    emb1 = _make_embedding(seed=10)
    emb2 = _similar_embedding(emb1, similarity=0.95, seed=20)
    emb3 = _similar_embedding(emb2, similarity=0.95, seed=30)
    db.set_embedding(1, emb1.tobytes())
    db.set_embedding(2, emb2.tobytes())
    db.set_embedding(3, emb3.tobytes())
    db.commit()

    n = _run_clip_merge(db, [], {})
    assert n == 2, f"Expected 2 merges (3 clusters → 1), got {n}"

    rows = db.conn.execute("SELECT DISTINCT cluster_id FROM photos").fetchall()
    assert len(rows) == 1, f"Expected 1 cluster, got {len(rows)}"
    assert rows[0]["cluster_id"] == 1, "Should keep lowest cluster id"


def test_clip_merge_gps_too_far():
    """GPS > 50m (both have GPS) → NO merge even with high CLIP."""
    db = _make_db()
    # ~1km apart
    _setup_photo_cluster(db, 1, 1, exif_date="2024:01:01 12:00:00", lat=55.0, lon=37.0)
    _setup_photo_cluster(db, 2, 2, exif_date="2024:01:01 12:01:00", lat=55.01, lon=37.0)

    emb1 = _make_embedding(seed=10)
    emb2 = _similar_embedding(emb1, similarity=0.99, seed=20)
    db.set_embedding(1, emb1.tobytes())
    db.set_embedding(2, emb2.tobytes())
    db.commit()

    n = _run_clip_merge(db, [], {})
    assert n == 0, f"Expected 0 merges (GPS > 50m), got {n}"


def test_clip_merge_skip_user_override():
    """Photos with user_cluster_override are excluded from merge."""
    db = _make_db()
    _setup_photo_cluster(db, 1, 1, exif_date="2024:01:01 12:00:00", user_cluster_override=1)
    _setup_photo_cluster(db, 2, 2, exif_date="2024:01:01 12:00:30")

    emb1 = _make_embedding(seed=10)
    emb2 = _similar_embedding(emb1, similarity=0.99, seed=20)
    db.set_embedding(1, emb1.tobytes())
    db.set_embedding(2, emb2.tobytes())
    db.commit()

    n = _run_clip_merge(db, [], {})
    assert n == 0, f"Expected 0 merges (user override), got {n}"


def test_clip_merge_uses_batch_embeddings():
    """Embeddings passed in-memory (current batch) are used for merge."""
    db = _make_db()
    _setup_photo_cluster(db, 1, 1, exif_date="2024:01:01 12:00:00")
    _setup_photo_cluster(db, 2, 2, exif_date="2024:01:01 12:01:00")
    db.commit()

    emb1 = _make_embedding(seed=10)
    emb2 = _similar_embedding(emb1, similarity=0.95, seed=20)
    # Pass embeddings in-memory, NOT stored in DB
    n = _run_clip_merge(db, [], {1: emb1, 2: emb2})
    assert n == 1, f"Expected 1 merge from in-memory embeddings, got {n}"
