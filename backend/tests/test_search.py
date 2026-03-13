"""Tests for CLIP text search with technical photo penalty."""

import numpy as np
import pytest

from photogal import search

DIM = 768


def _make_unit(index: int) -> np.ndarray:
    """Unit vector along given dimension."""
    v = np.zeros(DIM, dtype=np.float32)
    v[index] = 1.0
    return v


def _emb_with_similarity(query: np.ndarray, similarity: float, ortho_index: int = 1) -> bytes:
    """Create an L2-normalized embedding with exact cosine similarity to query.

    Uses: emb = sim * query + sqrt(1-sim²) * orthogonal_unit_vector
    """
    ortho = _make_unit(ortho_index)
    s = np.float32(similarity)
    emb = s * query + np.sqrt(1.0 - s * s) * ortho
    emb /= np.linalg.norm(emb)
    return emb.astype(np.float32).tobytes()


class FakeDB:
    """Minimal DB stub returning pre-configured embeddings."""

    def __init__(self, rows: list[tuple[int, bytes, int]]):
        self._rows = rows

    def get_all_embeddings(self) -> list[tuple[int, bytes, int]]:
        return self._rows


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure search cache is clean before each test."""
    search.invalidate_cache()
    yield
    search.invalidate_cache()


class TestTechnicalPenalty:
    """Technical photos get TECHNICAL_PENALTY subtracted from similarity."""

    def test_technical_ranked_below_content_at_equal_raw_similarity(self):
        """A technical photo with the same raw similarity as a content photo
        should appear lower in results after the penalty."""
        q = _make_unit(0)
        # Both embeddings have raw cosine ~0.30 with q, using different ortho axes
        emb_tech = _emb_with_similarity(q, 0.30, ortho_index=1)
        emb_content = _emb_with_similarity(q, 0.30, ortho_index=2)

        db = FakeDB([
            (1, emb_tech, 1),      # technical
            (2, emb_content, 0),   # content
        ])
        results = search.search(db, q)
        result_ids = [r["photo_id"] for r in results]

        # Content photo (0.30) should appear; technical (0.30 - 0.10 = 0.20) also above 0.18
        assert 2 in result_ids, "Content photo should appear in results"
        assert 1 in result_ids, "Technical photo should still appear (0.20 > 0.18)"
        idx_content = result_ids.index(2)
        idx_tech = result_ids.index(1)
        assert idx_content < idx_tech, "Content photo should rank above technical"

    def test_high_similarity_technical_still_passes(self):
        """A technical photo with very high raw similarity should still appear."""
        q = _make_unit(0)
        emb = _emb_with_similarity(q, 0.40, ortho_index=1)

        db = FakeDB([(1, emb, 1)])
        results = search.search(db, q)
        assert len(results) == 1
        assert results[0]["photo_id"] == 1
        # Penalized: 0.40 - 0.10 = 0.30, above 0.18
        assert results[0]["similarity"] >= search.MIN_SIMILARITY
        assert results[0]["similarity"] < 0.40

    def test_marginal_technical_cut_by_penalty(self):
        """A technical photo just above MIN_SIMILARITY in raw score
        should be cut after penalty is applied."""
        q = _make_unit(0)
        # Raw similarity 0.22 → penalized 0.22 - 0.10 = 0.12 < 0.18
        emb = _emb_with_similarity(q, 0.22, ortho_index=1)

        db = FakeDB([(1, emb, 1)])
        results = search.search(db, q)
        assert len(results) == 0, "Marginal technical photo should be filtered out"

    def test_non_technical_not_penalized(self):
        """Content photos should not be affected by the penalty."""
        q = _make_unit(0)
        emb = _emb_with_similarity(q, 0.22, ortho_index=1)

        db = FakeDB([(1, emb, 0)])
        results = search.search(db, q)
        assert len(results) == 1
        assert results[0]["photo_id"] == 1
        assert results[0]["similarity"] >= 0.20

    def test_empty_db(self):
        """Search with no embeddings should return empty list."""
        db = FakeDB([])
        q = np.random.randn(DIM).astype(np.float32)
        results = search.search(db, q)
        assert results == []
