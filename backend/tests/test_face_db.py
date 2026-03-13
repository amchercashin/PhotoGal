"""Tests for face-related database tables and methods."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import sqlite3
import numpy as np
from photogal.db import Database


def _make_db() -> Database:
    return Database(":memory:")


def test_faces_table_exists():
    db = _make_db()
    tables = {r[0] for r in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "faces" in tables
    assert "face_embeddings" in tables
    assert "persons" in tables
    db.close()


def test_insert_face():
    db = _make_db()
    # Insert a source + photo first
    db.conn.execute("INSERT INTO sources (path, name) VALUES ('/tmp', 'test')")
    db.conn.execute(
        "INSERT INTO photos (source_id, content_hash, original_path, original_filename) "
        "VALUES (1, 'abc', '/tmp/a.jpg', 'a.jpg')"
    )
    db.conn.execute(
        "INSERT INTO faces (photo_id, bbox_x, bbox_y, bbox_w, bbox_h, confidence, source_size) "
        "VALUES (1, 0.3, 0.1, 0.2, 0.35, 0.98, 'thumbnail')"
    )
    db.commit()
    row = db.conn.execute("SELECT * FROM faces WHERE photo_id = 1").fetchone()
    assert row["bbox_x"] == 0.3
    assert row["confidence"] == 0.98
    db.close()


def test_face_embeddings_table():
    db = _make_db()
    db.conn.execute("INSERT INTO sources (path, name) VALUES ('/tmp', 'test')")
    db.conn.execute(
        "INSERT INTO photos (source_id, content_hash, original_path, original_filename) "
        "VALUES (1, 'abc', '/tmp/a.jpg', 'a.jpg')"
    )
    db.conn.execute(
        "INSERT INTO faces (photo_id, bbox_x, bbox_y, bbox_w, bbox_h, confidence, source_size) "
        "VALUES (1, 0.3, 0.1, 0.2, 0.35, 0.98, 'thumbnail')"
    )
    emb = np.random.randn(512).astype(np.float32)
    db.conn.execute(
        "INSERT INTO face_embeddings (face_id, embedding) VALUES (1, ?)",
        (emb.tobytes(),),
    )
    db.commit()
    row = db.conn.execute("SELECT * FROM face_embeddings WHERE face_id = 1").fetchone()
    loaded = np.frombuffer(row["embedding"], dtype=np.float32)
    assert loaded.shape == (512,)
    np.testing.assert_array_almost_equal(loaded, emb)
    db.close()


def test_persons_table():
    db = _make_db()
    db.conn.execute("INSERT INTO persons (name, face_count) VALUES ('Маша', 5)")
    db.commit()
    row = db.conn.execute("SELECT * FROM persons WHERE id = 1").fetchone()
    assert row["name"] == "Маша"
    assert row["face_count"] == 5
    assert row["hidden"] == 0
    db.close()


def test_face_cascade_delete():
    """Deleting a photo cascades to faces and face_embeddings."""
    db = _make_db()
    db.conn.execute("INSERT INTO sources (path, name) VALUES ('/tmp', 'test')")
    db.conn.execute(
        "INSERT INTO photos (source_id, content_hash, original_path, original_filename) "
        "VALUES (1, 'abc', '/tmp/a.jpg', 'a.jpg')"
    )
    db.conn.execute(
        "INSERT INTO faces (photo_id, bbox_x, bbox_y, bbox_w, bbox_h, confidence, source_size) "
        "VALUES (1, 0.3, 0.1, 0.2, 0.35, 0.98, 'thumbnail')"
    )
    emb = np.zeros(512, dtype=np.float32)
    db.conn.execute("INSERT INTO face_embeddings (face_id, embedding) VALUES (1, ?)", (emb.tobytes(),))
    db.commit()
    db.conn.execute("DELETE FROM photos WHERE id = 1")
    db.commit()
    assert db.conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 0
    db.close()


# --- Task 2: DB helper method tests ---

def _setup_photo(db: Database) -> int:
    """Insert a source + photo, return photo_id."""
    db.conn.execute("INSERT INTO sources (path, name) VALUES ('/tmp', 'test')")
    db.conn.execute(
        "INSERT INTO photos (source_id, content_hash, original_path, original_filename, processing_level) "
        "VALUES (1, 'abc', '/tmp/a.jpg', 'a.jpg', 2)"
    )
    db.commit()
    return 1


def test_insert_faces_batch():
    db = _make_db()
    photo_id = _setup_photo(db)
    faces_data = [
        {"photo_id": photo_id, "bbox_x": 0.1, "bbox_y": 0.2, "bbox_w": 0.15, "bbox_h": 0.25,
         "confidence": 0.95, "source_size": "thumbnail"},
        {"photo_id": photo_id, "bbox_x": 0.5, "bbox_y": 0.1, "bbox_w": 0.2, "bbox_h": 0.3,
         "confidence": 0.88, "source_size": "original"},
    ]
    embeddings = [np.random.randn(512).astype(np.float32) for _ in range(2)]
    face_ids = db.insert_faces_batch(faces_data, embeddings)
    assert len(face_ids) == 2
    rows = db.conn.execute("SELECT COUNT(*) FROM faces WHERE photo_id = ?", (photo_id,)).fetchone()
    assert rows[0] == 2
    emb_count = db.conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0]
    assert emb_count == 2
    db.close()


def test_get_faces_by_photo():
    db = _make_db()
    photo_id = _setup_photo(db)
    faces_data = [
        {"photo_id": photo_id, "bbox_x": 0.1, "bbox_y": 0.2, "bbox_w": 0.15, "bbox_h": 0.25,
         "confidence": 0.95, "source_size": "thumbnail"},
    ]
    embeddings = [np.random.randn(512).astype(np.float32)]
    db.insert_faces_batch(faces_data, embeddings)
    faces = db.get_faces_by_photo(photo_id)
    assert len(faces) == 1
    assert faces[0]["bbox_x"] == 0.1
    assert faces[0]["person_name"] is None  # no person assigned yet
    db.close()


def test_create_person_and_assign():
    db = _make_db()
    photo_id = _setup_photo(db)
    faces_data = [
        {"photo_id": photo_id, "bbox_x": 0.1, "bbox_y": 0.2, "bbox_w": 0.15, "bbox_h": 0.25,
         "confidence": 0.95, "source_size": "thumbnail"},
    ]
    emb = np.random.randn(512).astype(np.float32)
    face_ids = db.insert_faces_batch(faces_data, [emb])
    person_id = db.create_person(face_count=1, representative_face_id=face_ids[0],
                                  centroid=emb)
    db.assign_faces_to_person(face_ids, person_id)
    face = db.get_faces_by_photo(photo_id)[0]
    assert face["person_id"] == person_id
    db.close()


def test_get_all_face_embeddings():
    db = _make_db()
    photo_id = _setup_photo(db)
    emb1 = np.random.randn(512).astype(np.float32)
    emb2 = np.random.randn(512).astype(np.float32)
    faces_data = [
        {"photo_id": photo_id, "bbox_x": 0.1, "bbox_y": 0.2, "bbox_w": 0.15, "bbox_h": 0.25,
         "confidence": 0.95, "source_size": "thumbnail"},
        {"photo_id": photo_id, "bbox_x": 0.5, "bbox_y": 0.1, "bbox_w": 0.2, "bbox_h": 0.3,
         "confidence": 0.88, "source_size": "thumbnail"},
    ]
    db.insert_faces_batch(faces_data, [emb1, emb2])
    face_ids, matrix = db.get_all_face_embeddings()
    assert len(face_ids) == 2
    assert matrix.shape == (2, 512)
    db.close()


def test_list_persons():
    db = _make_db()
    db.conn.execute("INSERT INTO persons (name, face_count) VALUES ('Маша', 10)")
    db.conn.execute("INSERT INTO persons (name, face_count) VALUES ('Петя', 5)")
    db.conn.execute("INSERT INTO persons (name, face_count, hidden) VALUES ('Random', 1, 1)")
    db.commit()
    persons = db.list_persons(include_hidden=False)
    assert len(persons) == 2
    assert persons[0]["name"] == "Маша"  # sorted by face_count DESC
    persons_all = db.list_persons(include_hidden=True)
    assert len(persons_all) == 3
    db.close()


def test_rename_person():
    db = _make_db()
    db.conn.execute("INSERT INTO persons (name, face_count) VALUES (NULL, 3)")
    db.commit()
    db.rename_person(1, "Вася")
    row = db.conn.execute("SELECT name FROM persons WHERE id = 1").fetchone()
    assert row["name"] == "Вася"
    db.close()


def test_get_person_photo_ids():
    db = _make_db()
    # 2 photos
    db.conn.execute("INSERT INTO sources (path, name) VALUES ('/tmp', 'test')")
    db.conn.execute(
        "INSERT INTO photos (source_id, content_hash, original_path, original_filename, exif_date) "
        "VALUES (1, 'a', '/tmp/a.jpg', 'a.jpg', '2024:06:01 12:00:00')"
    )
    db.conn.execute(
        "INSERT INTO photos (source_id, content_hash, original_path, original_filename, exif_date) "
        "VALUES (1, 'b', '/tmp/b.jpg', 'b.jpg', '2024:07:01 12:00:00')"
    )
    db.conn.execute("INSERT INTO persons (name, face_count) VALUES ('Маша', 2)")
    db.conn.execute(
        "INSERT INTO faces (photo_id, person_id, bbox_x, bbox_y, bbox_w, bbox_h, confidence, source_size) "
        "VALUES (1, 1, 0.1, 0.2, 0.15, 0.25, 0.95, 'thumbnail')"
    )
    db.conn.execute(
        "INSERT INTO faces (photo_id, person_id, bbox_x, bbox_y, bbox_w, bbox_h, confidence, source_size) "
        "VALUES (2, 1, 0.3, 0.1, 0.2, 0.3, 0.90, 'thumbnail')"
    )
    db.commit()
    photo_ids = db.get_person_photo_ids(1)
    assert photo_ids == [2, 1]  # sorted by exif_date DESC
    db.close()


def test_level3_migration():
    """Legacy level-3 photos (from embedder) get downgraded to level 2."""
    import tempfile, pathlib
    tmp = tempfile.mktemp(suffix=".db")
    try:
        # Phase 1: create DB with legacy level-3 data (raw sqlite, no migration)
        raw = sqlite3.connect(tmp)
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute("PRAGMA foreign_keys=ON")
        raw.executescript(
            "CREATE TABLE IF NOT EXISTS sources (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path TEXT NOT NULL UNIQUE, name TEXT, added_at TEXT, last_scanned_at TEXT, "
            "photo_count INTEGER DEFAULT 0, status TEXT DEFAULT 'idle');"
        )
        raw.executescript(
            "CREATE TABLE IF NOT EXISTS photos (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source_id INTEGER, content_hash TEXT NOT NULL, original_path TEXT NOT NULL, "
            "original_filename TEXT NOT NULL, current_path TEXT, file_size INTEGER, "
            "processing_level INTEGER DEFAULT 0, perceptual_hash TEXT, "
            "exif_date TEXT, exif_gps_lat REAL, exif_gps_lon REAL, exif_camera TEXT, "
            "exif_orientation INTEGER, exif_width INTEGER, exif_height INTEGER, "
            "cluster_id INTEGER, event_id INTEGER, quality_blur REAL, quality_exposure REAL, "
            "quality_aesthetic REAL, clip_embedding BLOB, face_count INTEGER, "
            "rank_in_cluster INTEGER, rank_in_event INTEGER, user_decision TEXT, "
            "user_cluster_override INTEGER, semantic_tags TEXT, content_category TEXT, "
            "is_technical INTEGER DEFAULT 0, moved_at TEXT, deleted_at TEXT, archived_at TEXT, "
            "location_country TEXT, location_city TEXT, location_district TEXT, "
            "sync_status TEXT DEFAULT 'ok', semantic_group_id INTEGER, "
            "is_exact_duplicate INTEGER DEFAULT 0, "
            "created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));"
        )
        raw.execute("INSERT INTO sources (path, name) VALUES ('/tmp', 'test')")
        raw.execute(
            "INSERT INTO photos (source_id, content_hash, original_path, original_filename, processing_level) "
            "VALUES (1, 'abc', '/tmp/a.jpg', 'a.jpg', 3)"
        )
        raw.commit()
        raw.close()
        # Phase 2: re-open with Database — migration should downgrade level 3→2
        db = Database(tmp)
        row = db.conn.execute("SELECT processing_level FROM photos WHERE id = 1").fetchone()
        assert row["processing_level"] == 2, "Legacy level 3 should be downgraded to 2"
        db.close()
    finally:
        pathlib.Path(tmp).unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            pathlib.Path(tmp + suffix).unlink(missing_ok=True)
