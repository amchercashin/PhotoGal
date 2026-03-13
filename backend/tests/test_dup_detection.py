"""Tests for L0 duplicate cluster detection in scanner."""
import sys
import os
import tempfile
import sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from photogal.db import Database
from photogal.config import Config
from photogal.pipeline.scanner import Scanner


def make_config():
    cfg = Config()
    return cfg


def _insert_photo(db, id_override=None, content_hash="abc123", filename="test.jpg",
                  cluster_id=None):
    """Insert a minimal photo row for testing."""
    cur = db.conn.execute(
        "INSERT INTO photos (original_path, original_filename, content_hash, processing_level) "
        "VALUES (?, ?, ?, 0)",
        (f"/fake/{filename}", filename, content_hash),
    )
    pid = cur.lastrowid
    if cluster_id is not None:
        db.conn.execute("UPDATE photos SET cluster_id=? WHERE id=?", (cluster_id, pid))
    db.commit()
    return pid


def test_dup_cluster_created_both_new():
    """Two new files with same SHA256 → dup cluster created."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "test.db"))
        scanner = Scanner(make_config())

        pid1 = _insert_photo(db, content_hash="SAMEHASH", filename="a.jpg")
        pid2 = _insert_photo(db, content_hash="SAMEHASH", filename="b.jpg")

        scanner._assign_dup_clusters(db, [pid1, pid2])

        photos = db.get_photos_by_ids([pid1, pid2])
        assert all(p["is_exact_duplicate"] == 1 for p in photos), "Both should be marked as duplicates"
        assert photos[0]["cluster_id"] == photos[1]["cluster_id"], "Both should be in same cluster"
        assert photos[0]["cluster_id"] is not None, "Cluster should be created"

        cluster = db.get_cluster_by_id(photos[0]["cluster_id"])
        assert cluster["type"] == "dup", f"Cluster type should be 'dup', got '{cluster['type']}'"
        db.close()


def test_dup_joins_existing_cluster():
    """New file duplicates existing (already in cluster) → joins existing cluster, type unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "test.db"))
        scanner = Scanner(make_config())

        # Create an existing cluster of type 'content'
        cid = db.create_cluster(name="existing", best_photo_id=None, photo_count=1, type="content")
        pid_existing = _insert_photo(db, content_hash="SAMEHASH", filename="existing.jpg", cluster_id=cid)
        db.update_cluster(cid, best_photo_id=pid_existing)
        db.commit()

        # New photo with same hash
        pid_new = _insert_photo(db, content_hash="SAMEHASH", filename="new.jpg")
        scanner._assign_dup_clusters(db, [pid_new])

        new_photo = db.get_photo(pid_new)
        assert new_photo["cluster_id"] == cid, "New photo should join existing cluster"
        assert new_photo["is_exact_duplicate"] == 1, "New photo should be marked as duplicate"

        cluster = db.get_cluster_by_id(cid)
        assert cluster["type"] == "content", "Existing cluster type must NOT change"
        db.close()


def test_no_dup_no_cluster():
    """Photo with unique hash → no dup cluster, no is_exact_duplicate."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "test.db"))
        scanner = Scanner(make_config())

        pid = _insert_photo(db, content_hash="UNIQUEHASH", filename="unique.jpg")
        scanner._assign_dup_clusters(db, [pid])

        photo = db.get_photo(pid)
        assert photo["is_exact_duplicate"] == 0, "Unique photo should not be a duplicate"
        assert photo["cluster_id"] is None, "Unique photo should not have a cluster"
        db.close()
