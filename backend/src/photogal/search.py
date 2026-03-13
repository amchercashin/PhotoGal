"""CLIP text search with in-memory embedding matrix cache."""

import threading

import numpy as np

from photogal.db import Database

_lock = threading.Lock()
_matrix: np.ndarray | None = None  # (N, 768) float32
_photo_ids: np.ndarray | None = None  # (N,) int64
_is_technical: np.ndarray | None = None  # (N,) bool

MIN_SIMILARITY = 0.18
TECHNICAL_PENALTY = 0.10


def _load_matrix(db: Database) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load all embeddings into numpy arrays."""
    rows = db.get_all_embeddings()
    if not rows:
        return (np.empty((0, 768), dtype=np.float32),
                np.empty(0, dtype=np.int64),
                np.empty(0, dtype=bool))
    ids = np.array([r[0] for r in rows], dtype=np.int64)
    tech = np.array([bool(r[2]) for r in rows], dtype=bool)
    embs = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    # L2-normalize (should already be, but ensure)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs = embs / norms
    return embs, ids, tech


def _ensure_cache(db: Database) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (matrix, photo_ids, is_technical), reloading if cache is empty."""
    global _matrix, _photo_ids, _is_technical
    # Fast path: if cache is warm, skip DB query entirely
    if _matrix is not None:
        return _matrix, _photo_ids, _is_technical  # type: ignore
    with _lock:
        # Double-check after acquiring lock
        if _matrix is not None:
            return _matrix, _photo_ids, _is_technical  # type: ignore
        _matrix, _photo_ids, _is_technical = _load_matrix(db)
        return _matrix, _photo_ids, _is_technical


def invalidate_cache():
    """Force cache reload on next search (call after L2 pipeline completes)."""
    global _matrix, _photo_ids, _is_technical
    with _lock:
        _matrix = None
        _photo_ids = None
        _is_technical = None


def get_cached_count() -> int:
    """Return the cached embedding count (0 if cache not yet loaded)."""
    ids = _photo_ids
    return len(ids) if ids is not None else 0


def search(
    db: Database,
    query_embedding: np.ndarray,
    limit: int = 200,
) -> list[dict]:
    """Search photos by cosine similarity to query embedding.

    Returns list of {"photo_id": int, "similarity": float} sorted desc.
    """
    matrix, photo_ids, is_technical = _ensure_cache(db)
    if matrix.shape[0] == 0:
        return []

    # query_embedding should be (768,) L2-normalized
    q = query_embedding.astype(np.float32)
    norm = np.linalg.norm(q)
    if norm > 0:
        q = q / norm

    # Cosine similarity = dot product (both L2-normalized)
    sims = matrix @ q  # (N,)

    # Penalize technical photos (screenshots, documents, etc.)
    if is_technical.any():
        sims = sims.copy()
        sims[is_technical] -= TECHNICAL_PENALTY

    # Filter by minimum threshold
    mask = sims >= MIN_SIMILARITY
    if not mask.any():
        return []

    filtered_sims = sims[mask]
    filtered_ids = photo_ids[mask]

    # Top-N sorted by similarity desc
    if len(filtered_sims) > limit:
        top_indices = np.argpartition(filtered_sims, -limit)[-limit:]
        filtered_sims = filtered_sims[top_indices]
        filtered_ids = filtered_ids[top_indices]

    order = np.argsort(-filtered_sims)
    return [
        {"photo_id": int(filtered_ids[i]), "similarity": round(float(filtered_sims[i]), 4)}
        for i in order
    ]
