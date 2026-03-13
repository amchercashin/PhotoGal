"""SQLite database: schema, queries, migrations."""

import contextlib
import sqlite3
import threading
from pathlib import Path


def _chunks(lst: list, n: int):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


# --- Column allowlists for dynamic UPDATE queries ---
SOURCE_UPDATABLE_COLS = frozenset({
    "status", "last_scanned_at", "photo_count", "name", "path",
})

PHOTO_UPDATABLE_COLS = frozenset({
    "processing_level", "cluster_id", "rank_in_cluster",
    "quality_aesthetic", "quality_blur", "quality_exposure",
    "content_category", "is_technical", "face_count",
    "perceptual_hash", "exif_date", "exif_gps_lat", "exif_gps_lon",
    "exif_camera", "exif_orientation", "exif_width", "exif_height",
    "location_country", "location_city", "location_district",
    "clip_embedding", "rank_in_event", "user_decision",
    "user_cluster_override", "semantic_tags", "semantic_group_id",
    "sync_status", "is_exact_duplicate", "current_path",
    "moved_at", "deleted_at", "archived_at", "source_id",
})

CLUSTER_UPDATABLE_COLS = frozenset({
    "name", "best_photo_id", "photo_count", "type",
    "avg_timestamp", "avg_gps_lat", "avg_gps_lon",
    "location_city", "event_id",
})


def _validate_update_cols(kwargs: dict, allowed: frozenset, table: str):
    """Raise ValueError if any key in kwargs is not in the allowed set."""
    bad = set(kwargs.keys()) - allowed
    if bad:
        raise ValueError(
            f"Invalid column(s) for {table} update: {bad}. "
            f"Allowed: {sorted(allowed)}"
        )


# Columns safe for ORDER BY — shared by all photo-listing / position queries.
PHOTO_SORTABLE_COLS = {
    "id", "exif_date", "original_filename", "file_size",
    "quality_blur", "quality_exposure", "quality_aesthetic",
    "processing_level", "content_category", "user_decision",
    "rank_in_cluster", "location_city", "location_country",
    "exif_gps_lat", "exif_gps_lon", "cluster_id",
    "is_exact_duplicate", "sync_status", "content_hash",
    "exif_camera",
}


def _validate_sort(sort_by: str, sort_dir: str) -> tuple[str, str]:
    if sort_by not in PHOTO_SORTABLE_COLS:
        sort_by = "exif_date"
    sort_dir = "DESC" if sort_dir.upper() == "DESC" else "ASC"
    return sort_by, sort_dir


def _build_photo_filter(
    *,
    filter_level: int | None = None,
    filter_category: str | None = None,
    filter_decision: str | None = None,
    filter_cluster_id: int | None = None,
) -> tuple[str, list]:
    """Build WHERE clause + params for photo queries."""
    conditions: list[str] = []
    params: list = []
    if filter_level is not None:
        conditions.append("processing_level >= ?")
        params.append(filter_level)
    if filter_category:
        conditions.append("content_category = ?")
        params.append(filter_category)
    if filter_decision:
        if filter_decision == "none":
            conditions.append("user_decision IS NULL")
        else:
            conditions.append("user_decision = ?")
            params.append(filter_decision)
    if filter_cluster_id is not None:
        conditions.append("cluster_id = ?")
        params.append(filter_cluster_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where, params


def resolve_photo_path(photo) -> str:
    """Return the effective file path for a photo row."""
    return photo["current_path"] or photo["original_path"]


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    name TEXT,
    added_at TEXT DEFAULT (datetime('now')),
    last_scanned_at TEXT,
    photo_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'idle'
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES sources(id),
    content_hash TEXT NOT NULL,
    perceptual_hash TEXT,
    original_path TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    current_path TEXT,
    file_size INTEGER,
    moved_at TEXT,
    deleted_at TEXT,
    archived_at TEXT,
    processing_level INTEGER DEFAULT 0,
    cluster_id INTEGER REFERENCES clusters(id),
    event_id INTEGER REFERENCES events(id),
    -- EXIF
    exif_date TEXT,
    exif_gps_lat REAL,
    exif_gps_lon REAL,
    exif_camera TEXT,
    exif_orientation INTEGER,
    exif_width INTEGER,
    exif_height INTEGER,
    -- Location (Level 1 geocoding)
    location_country TEXT,
    location_city TEXT,
    location_district TEXT,
    -- Quality (Level 1)
    quality_blur REAL,
    quality_exposure REAL,
    quality_aesthetic REAL,
    -- AI (Level 3)
    clip_embedding BLOB,
    face_count INTEGER,
    -- Ranking
    rank_in_cluster INTEGER,
    rank_in_event INTEGER,
    -- User decisions
    user_decision TEXT,              -- keep | delete | archive | null
    user_cluster_override INTEGER,
    -- Semantic layer (Level 3)
    semantic_tags TEXT,              -- JSON array
    content_category TEXT,           -- people | nature | food | travel | architecture | other
    is_technical INTEGER DEFAULT 0,
    --
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_photos_hash_path
    ON photos(content_hash, original_path);
CREATE INDEX IF NOT EXISTS idx_photos_cluster ON photos(cluster_id);
CREATE INDEX IF NOT EXISTS idx_photos_phash ON photos(perceptual_hash);
CREATE INDEX IF NOT EXISTS idx_photos_processing ON photos(processing_level);
CREATE INDEX IF NOT EXISTS idx_photos_source ON photos(source_id);
CREATE INDEX IF NOT EXISTS idx_photos_date ON photos(exif_date);

CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    best_photo_id INTEGER REFERENCES photos(id),
    photo_count INTEGER DEFAULT 0,
    type TEXT DEFAULT 'content',
    avg_timestamp TEXT,
    avg_gps_lat REAL,
    avg_gps_lon REAL,
    location_city TEXT,
    event_id INTEGER REFERENCES events(id),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    start_date TEXT,
    end_date TEXT,
    gps_lat REAL,
    gps_lon REAL,
    cluster_count INTEGER DEFAULT 0,
    description TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id INTEGER NOT NULL REFERENCES photos(id),
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type TEXT NOT NULL,
    level INTEGER,
    source_path TEXT,
    dest_path TEXT,
    photo_id INTEGER REFERENCES photos(id),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS perf_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    duration_s REAL NOT NULL,
    items INTEGER DEFAULT 0,
    items_label TEXT DEFAULT 'items',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS photo_embeddings (
    photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS persons (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT,
    face_count              INTEGER NOT NULL DEFAULT 0,
    representative_face_id  INTEGER,
    centroid                BLOB,
    hidden                  INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_persons_hidden ON persons(hidden);

CREATE TABLE IF NOT EXISTS faces (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id        INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    person_id       INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    bbox_x          REAL NOT NULL,
    bbox_y          REAL NOT NULL,
    bbox_w          REAL NOT NULL,
    bbox_h          REAL NOT NULL,
    confidence      REAL NOT NULL,
    source_size     TEXT NOT NULL DEFAULT 'thumbnail'
);
CREATE INDEX IF NOT EXISTS idx_faces_photo_id ON faces(photo_id);
CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces(person_id);

CREATE TABLE IF NOT EXISTS face_embeddings (
    face_id     INTEGER PRIMARY KEY REFERENCES faces(id) ON DELETE CASCADE,
    embedding   BLOB NOT NULL
);
"""

_MIGRATIONS = [
    # Add columns not present in older DBs
    ("photos", "source_id", "INTEGER"),
    ("photos", "location_country", "TEXT"),
    ("photos", "location_city", "TEXT"),
    ("photos", "location_district", "TEXT"),
    ("photos", "archived_at", "TEXT"),
    ("photos", "user_decision", "TEXT"),
    ("photos", "user_cluster_override", "INTEGER"),
    ("photos", "semantic_tags", "TEXT"),
    ("photos", "content_category", "TEXT"),
    ("photos", "is_technical", "INTEGER DEFAULT 0"),
    ("clusters", "location_city", "TEXT"),
    ("photos", "sync_status", "TEXT DEFAULT 'ok'"),
    ("photos", "semantic_group_id", "INTEGER"),
    ("photos", "is_exact_duplicate", "INTEGER DEFAULT 0"),
]

_MIGRATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_photos_sync_status ON photos(sync_status)",
]


class _MaterializedCursor:
    """Cursor-like object with results already fetched (thread-safe after creation)."""

    __slots__ = ("_rows", "_idx", "lastrowid", "rowcount", "description")

    def __init__(self, cursor: sqlite3.Cursor):
        self._rows = cursor.fetchall()
        self._idx = 0
        self.lastrowid = cursor.lastrowid
        self.rowcount = cursor.rowcount
        self.description = cursor.description

    def fetchone(self):
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
            self._idx += 1
            return row
        return None

    def fetchall(self):
        rows = self._rows[self._idx:]
        self._idx = len(self._rows)
        return rows

    def __iter__(self):
        return iter(self._rows)


class _ThreadSafeConn:
    """Wraps a sqlite3.Connection with a lock.

    execute() acquires the lock, runs the query, materialises all results,
    then releases the lock — so callers can safely use .fetchone()/.fetchall()
    from any thread without races.
    """

    def __init__(self, raw: sqlite3.Connection, lock: threading.Lock):
        self._raw = raw
        self._lock = lock
        # Thread-local flag: when True, execute/executemany skip lock acquisition
        # because the lock is already held by transaction().
        self._tlocal = threading.local()

    @property
    def _in_transaction(self) -> bool:
        return getattr(self._tlocal, "in_transaction", False)

    # --- delegated properties ---
    @property
    def row_factory(self):
        return self._raw.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._raw.row_factory = value

    # --- thread-safe operations ---
    def execute(self, sql, params=()):
        if self._in_transaction:
            cursor = self._raw.execute(sql, params)
            return _MaterializedCursor(cursor)
        with self._lock:
            cursor = self._raw.execute(sql, params)
            return _MaterializedCursor(cursor)

    def executemany(self, sql, params_seq):
        if self._in_transaction:
            cursor = self._raw.executemany(sql, params_seq)
            return _MaterializedCursor(cursor)
        with self._lock:
            cursor = self._raw.executemany(sql, params_seq)
            return _MaterializedCursor(cursor)

    def executescript(self, sql):
        if self._in_transaction:
            self._raw.executescript(sql)
        else:
            with self._lock:
                self._raw.executescript(sql)

    def commit(self):
        if self._in_transaction:
            self._raw.commit()
        else:
            with self._lock:
                self._raw.commit()

    def close(self):
        self._raw.close()

    @contextlib.contextmanager
    def transaction(self):
        """Context manager for atomic multi-step operations.

        Acquires the lock, issues BEGIN IMMEDIATE, and on success commits;
        on exception rolls back. The lock is held for the entire block.
        Regular execute/commit calls inside the block skip lock acquisition
        via a thread-local flag.
        """
        self._lock.acquire()
        try:
            self._tlocal.in_transaction = True
            self._raw.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._raw.commit()
            except BaseException:
                self._raw.rollback()
                raise
        finally:
            self._tlocal.in_transaction = False
            self._lock.release()


class Database:
    """SQLite database wrapper with WAL mode."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        raw = sqlite3.connect(str(self.path), check_same_thread=False)
        raw.row_factory = sqlite3.Row
        self.conn = _ThreadSafeConn(raw, self._lock)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA cache_size=-32000")  # 32MB cache
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        cols_cache: dict[str, set[str]] = {}
        for table, col, col_type in _MIGRATIONS:
            if table not in cols_cache:
                cols_cache[table] = {
                    row[1]
                    for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
            if col not in cols_cache[table]:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                cols_cache[table].add(col)
        for idx_sql in _MIGRATION_INDEXES:
            self.conn.execute(idx_sql)
        # Migrate clip_embedding data from photos to photo_embeddings
        photos_cols = cols_cache.get("photos") or {
            row[1] for row in self.conn.execute("PRAGMA table_info(photos)").fetchall()
        }
        self._migrate_embeddings(photos_cols)
        # Reclaim level 3 for face analysis: legacy embedder set level=3, downgrade to 2
        count_l3 = self.conn.execute(
            "SELECT COUNT(*) FROM photos WHERE processing_level = 3"
        ).fetchone()[0]
        if count_l3 > 0:
            self.conn.execute(
                "UPDATE photos SET processing_level = 2 WHERE processing_level = 3"
            )

    def _migrate_embeddings(self, photos_cols: set[str]):
        """Move clip_embedding from photos to photo_embeddings (one-time migration)."""
        has_clip_col = "clip_embedding" in photos_cols
        if not has_clip_col:
            return
        count = self.conn.execute(
            "SELECT COUNT(*) FROM photos WHERE clip_embedding IS NOT NULL"
        ).fetchone()[0]
        if count > 0:
            self.conn.execute(
                "INSERT OR IGNORE INTO photo_embeddings (photo_id, embedding) "
                "SELECT id, clip_embedding FROM photos WHERE clip_embedding IS NOT NULL"
            )
            self.conn.execute("UPDATE photos SET clip_embedding = NULL")

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def commit(self):
        self.conn.commit()

    # --- Sources ---

    def add_source(self, path: str, name: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO sources (path, name) VALUES (?, ?)",
            (path, name or Path(path).name),
        )
        if cur.lastrowid:
            self.conn.commit()
            return cur.lastrowid
        row = self.conn.execute("SELECT id FROM sources WHERE path = ?", (path,)).fetchone()
        return row["id"]

    def get_source(self, source_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()

    def get_all_sources(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM sources ORDER BY added_at DESC").fetchall()

    def update_source(self, source_id: int, **kwargs):
        _validate_update_cols(kwargs, SOURCE_UPDATABLE_COLS, "sources")
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        self.conn.execute(f"UPDATE sources SET {sets} WHERE id = ?", (*kwargs.values(), source_id))

    # --- Photos ---

    def photo_exists(self, content_hash: str, original_path: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM photos WHERE content_hash = ? AND original_path = ?",
            (content_hash, original_path),
        ).fetchone()
        return row is not None

    def insert_photo(self, **kwargs) -> int:
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" for _ in kwargs)
        cur = self.conn.execute(
            f"INSERT INTO photos ({cols}) VALUES ({placeholders})",
            tuple(kwargs.values()),
        )
        return cur.lastrowid

    def insert_photos_batch(self, photos: list[dict]) -> list[int]:
        if not photos:
            return []
        cols = ", ".join(photos[0].keys())
        placeholders = ", ".join("?" for _ in photos[0])
        sql = f"INSERT INTO photos ({cols}) VALUES ({placeholders})"
        ids = []
        with self.conn.transaction():
            for p in photos:
                cursor = self.conn.execute(sql, tuple(p.values()))
                ids.append(cursor.lastrowid)
        return ids

    def update_photo(self, photo_id: int, **kwargs):
        _validate_update_cols(kwargs, PHOTO_UPDATABLE_COLS, "photos")
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        self.conn.execute(
            f"UPDATE photos SET {sets}, updated_at = datetime('now') WHERE id = ?",
            (*kwargs.values(), photo_id),
        )

    def get_photo(self, photo_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()

    def get_photos_by_ids(self, photo_ids: list[int]) -> list[sqlite3.Row]:
        placeholders = ",".join("?" * len(photo_ids))
        return self.conn.execute(
            f"SELECT * FROM photos WHERE id IN ({placeholders})", photo_ids
        ).fetchall()

    def get_all_photos(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM photos ORDER BY exif_date, id").fetchall()

    def get_photos_by_cluster(self, cluster_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM photos WHERE cluster_id = ? ORDER BY rank_in_cluster, id",
            (cluster_id,),
        ).fetchall()

    def get_unprocessed_photos(self, level: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM photos WHERE processing_level < ?", (level,)
        ).fetchall()

    def get_all_photo_paths(self) -> list[tuple[int, str]]:
        """Return (id, path) for all photos (uses current_path if set, else original_path)."""
        rows = self.conn.execute(
            "SELECT id, original_path, current_path FROM photos"
        ).fetchall()
        return [(r["id"], r["current_path"] or r["original_path"]) for r in rows]

    def update_photos_batch(self, column_names: list[str], updates: list[tuple]):
        """Batch update photos. Each tuple in updates = (*column_values, photo_id)."""
        if not updates:
            return
        sets = ", ".join(f"{col} = ?" for col in column_names)
        self.conn.executemany(
            f"UPDATE photos SET {sets}, updated_at = datetime('now') WHERE id = ?",
            updates,
        )

    def get_photos_for_ranking(self) -> list[sqlite3.Row]:
        """Lightweight query for ranking — only the columns needed."""
        return self.conn.execute(
            "SELECT id, cluster_id, quality_aesthetic, quality_blur, "
            "quality_exposure, original_filename FROM photos "
            "WHERE cluster_id IS NOT NULL"
        ).fetchall()

    def update_sync_status_bulk(self, updates: list[tuple[str, int]]):
        """Update sync_status for multiple photos: list of (status, photo_id)."""
        self.conn.executemany(
            "UPDATE photos SET sync_status = ? WHERE id = ?", updates
        )

    def count_disconnected(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM photos WHERE sync_status = 'disconnected'"
        ).fetchone()[0]

    def get_photo_ids_by_level(self, level: int) -> list[int]:
        """Return IDs of photos at exactly the given processing_level."""
        rows = self.conn.execute(
            "SELECT id FROM photos WHERE processing_level = ?", (level,)
        ).fetchall()
        return [r["id"] for r in rows]

    def get_photo_ids_by_sync_status(self, status: str) -> list[int]:
        rows = self.conn.execute(
            "SELECT id FROM photos WHERE sync_status = ?", (status,)
        ).fetchall()
        return [r["id"] for r in rows]

    def count_photos(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]

    def get_photos_paginated(
        self,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "exif_date",
        sort_dir: str = "ASC",
        filter_level: int | None = None,
        filter_category: str | None = None,
        filter_decision: str | None = None,
        filter_cluster_id: int | None = None,
    ) -> list[sqlite3.Row]:
        sort_by, sort_dir = _validate_sort(sort_by, sort_dir)
        where, params = _build_photo_filter(
            filter_level=filter_level, filter_category=filter_category,
            filter_decision=filter_decision, filter_cluster_id=filter_cluster_id,
        )
        params += [limit, offset]
        return self.conn.execute(
            f"SELECT * FROM photos {where} ORDER BY {sort_by} {sort_dir} LIMIT ? OFFSET ?",
            params,
        ).fetchall()

    def count_photos_filtered(
        self,
        filter_level: int | None = None,
        filter_category: str | None = None,
        filter_decision: str | None = None,
        filter_cluster_id: int | None = None,
    ) -> int:
        where, params = _build_photo_filter(
            filter_level=filter_level, filter_category=filter_category,
            filter_decision=filter_decision, filter_cluster_id=filter_cluster_id,
        )
        return self.conn.execute(f"SELECT COUNT(*) FROM photos {where}", params).fetchone()[0]

    def get_photo_table_position(
        self,
        photo_id: int,
        sort_by: str = "exif_date",
        sort_dir: str = "ASC",
        filter_category: str | None = None,
        page_size: int = 100,
    ) -> dict:
        """Return the page number where photo_id appears given sort/filter params."""
        sort_by, sort_dir = _validate_sort(sort_by, sort_dir)
        where, params = _build_photo_filter(filter_category=filter_category)

        params.append(photo_id)
        row = self.conn.execute(
            f"""
            SELECT rn - 1 as row_index FROM (
                SELECT id, ROW_NUMBER() OVER (ORDER BY {sort_by} {sort_dir}) as rn
                FROM photos
                {where}
            ) sub WHERE id = ?
            """,
            params,
        ).fetchone()

        if row is None:
            return {"row_index": 0, "page": 0, "found": False}
        row_index = row["row_index"]
        return {"row_index": row_index, "page": row_index // page_size, "found": True}

    # --- Duplicate detection ---

    def get_exact_duplicate_groups(self) -> list[list[sqlite3.Row]]:
        hashes = self.conn.execute(
            "SELECT content_hash FROM photos GROUP BY content_hash HAVING COUNT(*) > 1"
        ).fetchall()
        groups = []
        for row in hashes:
            photos = self.conn.execute(
                "SELECT * FROM photos WHERE content_hash = ? ORDER BY id",
                (row["content_hash"],),
            ).fetchall()
            groups.append(photos)
        return groups

    def get_all_perceptual_hashes(self) -> list[tuple[int, str, str | None, float | None]]:
        """Returns (id, phash, exif_date, gps_lat, gps_lon) for similarity clustering."""
        rows = self.conn.execute(
            "SELECT id, perceptual_hash, exif_date, exif_gps_lat, exif_gps_lon "
            "FROM photos WHERE perceptual_hash IS NOT NULL"
        ).fetchall()
        return [(r["id"], r["perceptual_hash"], r["exif_date"], r["exif_gps_lat"], r["exif_gps_lon"])
                for r in rows]

    # --- Clusters ---

    def create_cluster(self, **kwargs) -> int:
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" for _ in kwargs)
        cur = self.conn.execute(
            f"INSERT INTO clusters ({cols}) VALUES ({placeholders})",
            tuple(kwargs.values()),
        )
        return cur.lastrowid

    def update_cluster(self, cluster_id: int, **kwargs):
        _validate_update_cols(kwargs, CLUSTER_UPDATABLE_COLS, "clusters")
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        self.conn.execute(
            f"UPDATE clusters SET {sets} WHERE id = ?",
            (*kwargs.values(), cluster_id),
        )

    def get_all_clusters(self, nonempty: bool = False) -> list[sqlite3.Row]:
        if nonempty:
            return self.conn.execute(
                "SELECT c.*, GROUP_CONCAT(p.id) AS photo_ids_str, "
                "bp.quality_blur AS best_photo_blur, bp.quality_exposure AS best_photo_exposure, "
                "MAX(p.is_exact_duplicate) AS has_exact_duplicate "
                "FROM clusters c "
                "INNER JOIN photos p ON p.cluster_id = c.id "
                "LEFT JOIN photos bp ON c.best_photo_id = bp.id "
                "GROUP BY c.id "
                "ORDER BY c.avg_timestamp, c.id"
            ).fetchall()
        return self.conn.execute(
            "SELECT c.*, GROUP_CONCAT(p.id) AS photo_ids_str, "
            "bp.quality_blur AS best_photo_blur, bp.quality_exposure AS best_photo_exposure, "
            "MAX(p.is_exact_duplicate) AS has_exact_duplicate "
            "FROM clusters c "
            "LEFT JOIN photos p ON p.cluster_id = c.id "
            "LEFT JOIN photos bp ON c.best_photo_id = bp.id "
            "GROUP BY c.id "
            "ORDER BY c.avg_timestamp, c.id"
        ).fetchall()

    def get_clusters_paginated(
        self,
        limit: int = 100,
        offset: int = 0,
        nonempty: bool = True,
    ) -> tuple[list[sqlite3.Row], int]:
        """Return (clusters, total_count) without photo_ids — lightweight for listing."""
        if nonempty:
            base_where = "INNER JOIN photos p ON p.cluster_id = c.id"
            count_where = "WHERE EXISTS (SELECT 1 FROM photos WHERE cluster_id = c.id)"
        else:
            base_where = "LEFT JOIN photos p ON p.cluster_id = c.id"
            count_where = ""

        total = self.conn.execute(
            f"SELECT COUNT(*) FROM clusters c {count_where}"
        ).fetchone()[0]

        rows = self.conn.execute(
            f"SELECT c.*, "
            f"bp.quality_blur AS best_photo_blur, bp.quality_exposure AS best_photo_exposure, "
            f"MAX(p.is_exact_duplicate) AS has_exact_duplicate, "
            f"COUNT(p.id) AS actual_photo_count "
            f"FROM clusters c "
            f"{base_where} "
            f"LEFT JOIN photos bp ON c.best_photo_id = bp.id "
            f"GROUP BY c.id "
            f"ORDER BY c.avg_timestamp, c.id "
            f"LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return rows, total

    def get_photo_ids_by_cluster_ids(self, cluster_ids: list[int]) -> dict[int, list[int]]:
        """Return {cluster_id: [photo_ids]} for given cluster_ids."""
        if not cluster_ids:
            return {}
        result: dict[int, list[int]] = {cid: [] for cid in cluster_ids}
        for batch in _chunks(cluster_ids, 900):
            ph = ",".join("?" * len(batch))
            rows = self.conn.execute(
                f"SELECT id, cluster_id FROM photos WHERE cluster_id IN ({ph}) ORDER BY rank_in_cluster, id",
                batch,
            ).fetchall()
            for r in rows:
                result[r["cluster_id"]].append(r["id"])
        return result

    def merge_clusters(self, keep_id: int, absorb_id: int):
        """Merge cluster absorb_id into keep_id (atomic)."""
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE photos SET cluster_id = ? WHERE cluster_id = ?",
                (keep_id, absorb_id),
            )
            # Single aggregate query for count + GPS
            agg = self.conn.execute(
                "SELECT COUNT(*) AS cnt, "
                "AVG(CASE WHEN exif_gps_lat IS NOT NULL THEN exif_gps_lat END) AS lat, "
                "AVG(CASE WHEN exif_gps_lon IS NOT NULL THEN exif_gps_lon END) AS lon "
                "FROM photos WHERE cluster_id = ?",
                (keep_id,),
            ).fetchone()
            # Median timestamp via single query
            date_rows = self.conn.execute(
                "SELECT exif_date FROM photos WHERE cluster_id = ? AND exif_date IS NOT NULL ORDER BY exif_date",
                (keep_id,),
            ).fetchall()
            avg_ts = date_rows[len(date_rows) // 2]["exif_date"] if date_rows else None
            self.update_cluster(
                keep_id,
                photo_count=agg["cnt"],
                type="content",
                avg_timestamp=avg_ts,
                avg_gps_lat=float(agg["lat"]) if agg["lat"] else None,
                avg_gps_lon=float(agg["lon"]) if agg["lon"] else None,
            )
            self.conn.execute("DELETE FROM clusters WHERE id = ?", (absorb_id,))

    def get_photos_for_clip_merge(self, batch_ids: set[int]) -> list[sqlite3.Row]:
        """Get photos eligible for CLIP merge (level>=2 or in batch), with cluster type."""
        if batch_ids:
            ph = ",".join("?" * len(batch_ids))
            where = f"(p.processing_level >= 2 OR p.id IN ({ph}))"
            params = list(batch_ids)
        else:
            where = "p.processing_level >= 2"
            params = []
        return self.conn.execute(
            f"SELECT p.id, p.cluster_id, p.exif_date, p.exif_gps_lat, p.exif_gps_lon, "
            f"p.user_cluster_override, c.type AS cluster_type "
            f"FROM photos p "
            f"JOIN clusters c ON p.cluster_id = c.id "
            f"WHERE {where}",
            params,
        ).fetchall()

    def get_cluster_by_id(self, cluster_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM clusters WHERE id = ?", (cluster_id,)
        ).fetchone()

    def count_clusters(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]

    # --- User decisions ---

    def set_photo_decision(self, photo_id: int, decision: str | None):
        self.conn.execute(
            "UPDATE photos SET user_decision = ?, updated_at = datetime('now') WHERE id = ?",
            (decision, photo_id),
        )

    def set_photos_decision_bulk(self, photo_ids: list[int], decision: str | None):
        placeholders = ",".join("?" for _ in photo_ids)
        self.conn.execute(
            f"UPDATE photos SET user_decision = ?, updated_at = datetime('now') "
            f"WHERE id IN ({placeholders})",
            [decision, *photo_ids],
        )

    def get_decisions_summary(self) -> dict:
        rows = self.conn.execute(
            "SELECT user_decision, COUNT(*) as cnt FROM photos "
            "WHERE user_decision IS NOT NULL GROUP BY user_decision"
        ).fetchall()
        return {r["user_decision"]: r["cnt"] for r in rows}

    def move_photo_to_cluster(self, photo_id: int, cluster_id: int):
        self.conn.execute(
            "UPDATE photos SET cluster_id = ?, user_cluster_override = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (cluster_id, cluster_id, photo_id),
        )

    def delete_photos_bulk(self, photo_ids: list[int]):
        with self.conn.transaction():
            for batch in _chunks(photo_ids, 900):
                ph = ",".join("?" * len(batch))
                self.conn.execute(f"DELETE FROM user_corrections WHERE photo_id IN ({ph})", batch)
                self.conn.execute(f"UPDATE operations SET photo_id = NULL WHERE photo_id IN ({ph})", batch)
                self.conn.execute(f"UPDATE clusters SET best_photo_id = NULL WHERE best_photo_id IN ({ph})", batch)
                self.conn.execute(f"DELETE FROM photo_embeddings WHERE photo_id IN ({ph})", batch)
                self.conn.execute(f"DELETE FROM photos WHERE id IN ({ph})", batch)

    # --- Embeddings ---

    def get_embedding(self, photo_id: int) -> bytes | None:
        row = self.conn.execute(
            "SELECT embedding FROM photo_embeddings WHERE photo_id = ?", (photo_id,)
        ).fetchone()
        return row["embedding"] if row else None

    def set_embedding(self, photo_id: int, embedding: bytes) -> bool:
        """Store embedding. Returns False if photo was deleted (FK violation)."""
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO photo_embeddings (photo_id, embedding) VALUES (?, ?)",
                (photo_id, embedding),
            )
            return True
        except Exception:
            return False

    def get_embeddings_by_ids(self, photo_ids: list[int]) -> dict[int, bytes]:
        if not photo_ids:
            return {}
        result = {}
        for batch in _chunks(photo_ids, 900):
            ph = ",".join("?" * len(batch))
            rows = self.conn.execute(
                f"SELECT photo_id, embedding FROM photo_embeddings WHERE photo_id IN ({ph})",
                batch,
            ).fetchall()
            for r in rows:
                result[r["photo_id"]] = r["embedding"]
        return result

    def set_embeddings_batch(self, items: list[tuple[int, bytes]]):
        self.conn.executemany(
            "INSERT OR REPLACE INTO photo_embeddings (photo_id, embedding) VALUES (?, ?)",
            items,
        )

    def get_all_embeddings(self) -> list[tuple[int, bytes, int]]:
        """All (photo_id, embedding_blob, is_technical) triples."""
        return [(r["photo_id"], r["embedding"], r["is_technical"] or 0)
                for r in self.conn.execute(
                    "SELECT pe.photo_id, pe.embedding, "
                    "COALESCE(p.is_technical, 0) AS is_technical "
                    "FROM photo_embeddings pe "
                    "JOIN photos p ON pe.photo_id = p.id"
                ).fetchall()]

    def count_embeddings(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM photo_embeddings").fetchone()[0]

    def get_photo_ids_by_source(self, source_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT id FROM photos WHERE source_id = ?", (source_id,)
        ).fetchall()
        return [r["id"] for r in rows]

    def cleanup_orphan_clusters(self):
        """Remove clusters that have no photos."""
        self.conn.execute(
            "DELETE FROM clusters WHERE id NOT IN "
            "(SELECT DISTINCT cluster_id FROM photos WHERE cluster_id IS NOT NULL)"
        )

    # --- Faces & Persons ---

    def insert_faces_batch(
        self, faces_data: list[dict], embeddings: list["np.ndarray"]
    ) -> list[int]:
        """Insert faces + embeddings in batch. Returns list of face IDs.

        Does NOT auto-commit; caller is responsible for calling db.commit().
        """
        face_ids = []
        for fd, emb in zip(faces_data, embeddings):
            cur = self.conn.execute(
                "INSERT INTO faces (photo_id, bbox_x, bbox_y, bbox_w, bbox_h, "
                "confidence, source_size) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (fd["photo_id"], fd["bbox_x"], fd["bbox_y"], fd["bbox_w"],
                 fd["bbox_h"], fd["confidence"], fd["source_size"]),
            )
            face_id = cur.lastrowid
            face_ids.append(face_id)
            self.conn.execute(
                "INSERT INTO face_embeddings (face_id, embedding) VALUES (?, ?)",
                (face_id, emb.tobytes()),
            )
        return face_ids

    def get_faces_by_photo(self, photo_id: int) -> list[dict]:
        """Get faces for a photo with person info (for Viewer overlay)."""
        rows = self.conn.execute(
            "SELECT f.id, f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h, "
            "f.confidence, f.person_id, p.name AS person_name "
            "FROM faces f LEFT JOIN persons p ON f.person_id = p.id "
            "WHERE f.photo_id = ?",
            (photo_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_face_embeddings(self) -> tuple[list[int], "np.ndarray"]:
        """Load all face embeddings for clustering. Returns (face_ids, matrix)."""
        import numpy as np
        rows = self.conn.execute(
            "SELECT fe.face_id, fe.embedding FROM face_embeddings fe"
        ).fetchall()
        if not rows:
            return [], np.zeros((0, 512), dtype=np.float32)
        face_ids = [r["face_id"] for r in rows]
        matrix = np.stack([
            np.frombuffer(r["embedding"], dtype=np.float32) for r in rows
        ])
        return face_ids, matrix

    def create_person(
        self, face_count: int, representative_face_id: int | None = None,
        centroid: "np.ndarray | None" = None, name: str | None = None,
    ) -> int:
        """Create a person record. Returns person_id."""
        cur = self.conn.execute(
            "INSERT INTO persons (name, face_count, representative_face_id, centroid) "
            "VALUES (?, ?, ?, ?)",
            (name, face_count, representative_face_id,
             centroid.tobytes() if centroid is not None else None),
        )
        return cur.lastrowid

    def assign_faces_to_person(self, face_ids: list[int], person_id: int):
        """Assign a list of face IDs to a person."""
        for chunk in _chunks(face_ids, 900):
            ph = ",".join("?" * len(chunk))
            self.conn.execute(
                f"UPDATE faces SET person_id = ? WHERE id IN ({ph})",
                [person_id] + chunk,
            )

    def list_persons(self, include_hidden: bool = False) -> list[dict]:
        """List persons sorted by face_count DESC."""
        sql = "SELECT * FROM persons"
        if not include_hidden:
            sql += " WHERE hidden = 0"
        sql += " ORDER BY face_count DESC"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def rename_person(self, person_id: int, name: str):
        """Rename a person."""
        self.conn.execute(
            "UPDATE persons SET name = ?, updated_at = datetime('now') WHERE id = ?",
            (name, person_id),
        )
        self.commit()

    def hide_person(self, person_id: int, hidden: bool = True):
        """Hide/unhide a person."""
        self.conn.execute(
            "UPDATE persons SET hidden = ?, updated_at = datetime('now') WHERE id = ?",
            (int(hidden), person_id),
        )
        self.commit()

    def get_person_photo_ids(self, person_id: int) -> list[int]:
        """Get photo IDs for a person, sorted by exif_date DESC."""
        rows = self.conn.execute(
            "SELECT DISTINCT p.id FROM photos p "
            "JOIN faces f ON f.photo_id = p.id "
            "WHERE f.person_id = ? "
            "ORDER BY p.exif_date DESC",
            (person_id,),
        ).fetchall()
        return [r["id"] for r in rows]

    def cleanup_orphaned_persons(self):
        """Delete persons with no faces, update face_count for all."""
        self.conn.execute(
            "UPDATE persons SET face_count = ("
            "  SELECT COUNT(*) FROM faces WHERE faces.person_id = persons.id"
            ")"
        )
        self.conn.execute("DELETE FROM persons WHERE face_count = 0")
        self.commit()

    def get_category_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT content_category, COUNT(*) as cnt FROM photos "
            "WHERE content_category IS NOT NULL GROUP BY content_category"
        ).fetchall()
        return {r["content_category"]: r["cnt"] for r in rows}

    # --- Stats ---

    def get_stats(self) -> dict:
        total = self.count_photos()
        by_level = {}
        for level in range(4):
            count = self.conn.execute(
                "SELECT COUNT(*) FROM photos WHERE processing_level = ?", (level,)
            ).fetchone()[0]
            by_level[level] = count
        clusters = self.count_clusters()
        marked = self.conn.execute(
            "SELECT COUNT(*) FROM photos WHERE user_decision IS NOT NULL"
        ).fetchone()[0]
        technical = self.conn.execute(
            "SELECT COUNT(*) FROM photos WHERE is_technical = 1"
        ).fetchone()[0]
        sources = self.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        disconnected = self.count_disconnected()
        category_counts = self.get_category_counts()
        return {
            "total_photos": total,
            "by_level": by_level,
            "clusters": clusters,
            "marked_photos": marked,
            "technical_photos": technical,
            "sources": sources,
            "disconnected": disconnected,
            "category_counts": category_counts,
        }

    # --- Perf log ---

    def log_perf(self, run_id: str, stage: str, duration_s: float,
                 items: int = 0, items_label: str = "items"):
        self.conn.execute(
            "INSERT INTO perf_log (run_id, stage, duration_s, items, items_label) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, stage, duration_s, items, items_label),
        )

    def log_operation(self, operation_type: str, level: int, source_path: str,
                      dest_path: str, photo_id: int | None = None):
        self.conn.execute(
            "INSERT INTO operations (operation_type, level, source_path, dest_path, photo_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (operation_type, level, source_path, dest_path, photo_id),
        )
