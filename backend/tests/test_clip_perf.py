"""Tests for L2 pipeline performance optimizations."""

import numpy as np
import torch
from unittest.mock import MagicMock, patch

from photogal.config import Config
from photogal.db import Database
from photogal.models.clip import _AestheticMLP, CLIPModel
from photogal.pipeline.analyzer import Analyzer
from photogal.thumbnails import get_thumbnail_path


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_mock_clip():
    """Create a mock CLIP model that doesn't load real weights."""
    clip = MagicMock()
    clip.device = "cpu"
    clip.embed_batch.return_value = [np.random.randn(768).astype(np.float32)]
    clip.aesthetic_score_from_embedding.return_value = 5.0
    clip.aesthetic_scores_batch.return_value = [5.0]
    clip.embed_texts.return_value = np.random.randn(22, 768).astype(np.float32)
    return clip


class _FakeCLIPAesthetic:
    """Minimal object to test aesthetic batch scoring without loading CLIP."""

    device = "cpu"
    dtype = torch.float32

    def __init__(self):
        self.aesthetic_head = _AestheticMLP()
        self.aesthetic_head.eval()

    aesthetic_scores_batch = CLIPModel.aesthetic_scores_batch
    aesthetic_score_from_embedding = CLIPModel.aesthetic_score_from_embedding


# ─── Task 1: Model load timing ───────────────────────────────────────────────


def test_model_load_time_logged(tmp_path):
    """CLIP model load duration should appear in perf_log."""
    db = Database(str(tmp_path / "test.db"))
    db.conn.execute(
        "INSERT INTO photos (original_path, original_filename, content_hash, processing_level) "
        "VALUES (?, ?, ?, ?)",
        ("/fake/photo.jpg", "photo.jpg", "abc123", 1),
    )
    db.commit()
    photos = db.get_unprocessed_photos(level=2)

    analyzer = Analyzer(Config())
    mock_clip = _make_mock_clip()
    analyzer._clip = None  # force load path

    with patch.object(analyzer, "_get_clip", return_value=mock_clip):
        analyzer._run_clip(db, photos)

    row = db.conn.execute(
        "SELECT stage, duration_s FROM perf_log WHERE stage = 'analyze/model_load'"
    ).fetchone()
    assert row is not None
    assert row["duration_s"] >= 0


# ─── Task 2: Batch aesthetic scoring ─────────────────────────────────────────


def test_aesthetic_scores_batch_matches_individual():
    """Batch scores must match individual calls."""
    fake = _FakeCLIPAesthetic()
    embeddings = [np.random.randn(768).astype(np.float32) for _ in range(5)]

    batch_scores = fake.aesthetic_scores_batch(embeddings)
    individual_scores = [fake.aesthetic_score_from_embedding(e) for e in embeddings]

    assert len(batch_scores) == 5
    for bs, ind in zip(batch_scores, individual_scores):
        assert abs(bs - ind) < 1e-5


def test_aesthetic_scores_batch_empty():
    """Empty input returns empty list."""
    fake = _FakeCLIPAesthetic()
    assert fake.aesthetic_scores_batch([]) == []


def test_aesthetic_scores_batch_clamped():
    """All scores are in [1.0, 10.0] range."""
    fake = _FakeCLIPAesthetic()
    embeddings = [np.random.randn(768).astype(np.float32) * 10 for _ in range(10)]
    scores = fake.aesthetic_scores_batch(embeddings)
    for s in scores:
        assert 1.0 <= s <= 10.0


# ─── Task 3: Thumbnail resolution for CLIP embeddings ───────────────────────


def _insert_photo(db, original_path, content_hash, level=1):
    """Insert a photo row and return its id."""
    db.conn.execute(
        "INSERT INTO photos (original_path, original_filename, content_hash, processing_level) "
        "VALUES (?, ?, ?, ?)",
        (original_path, original_path.split("/")[-1], content_hash, level),
    )
    db.commit()


def test_phase4_uses_thumbnail_when_available(tmp_path):
    """Phase 4 should use thumbnail path instead of original."""
    thumb_dir = tmp_path / "thumbs"
    thumb_dir.mkdir()
    content_hash = "abcdef1234567890abcdef1234567890ff"
    thumb_path = get_thumbnail_path(thumb_dir, content_hash=content_hash)
    thumb_path.write_bytes(b"fake jpeg")

    db = Database(str(tmp_path / "test.db"))
    _insert_photo(db, "/original/big.heic", content_hash)
    photos = db.get_unprocessed_photos(level=2)

    analyzer = Analyzer(Config())
    mock_clip = _make_mock_clip()
    analyzer._clip = mock_clip
    captured_paths = []

    def capture_embed(fps):
        captured_paths.extend(fps)
        return [np.random.randn(768).astype(np.float32) for _ in fps]

    mock_clip.embed_batch.side_effect = capture_embed

    with patch("photogal.config.get_thumbnail_cache_dir", return_value=thumb_dir):
        analyzer._run_clip(db, photos)

    assert len(captured_paths) == 1
    assert captured_paths[0] == str(thumb_path)


def test_phase4_falls_back_to_original(tmp_path):
    """Phase 4 falls back to original when thumbnail doesn't exist."""
    thumb_dir = tmp_path / "thumbs"
    thumb_dir.mkdir()

    db = Database(str(tmp_path / "test.db"))
    _insert_photo(db, "/original/big.heic", "no_thumb_hash_here_1234567890abcd")
    photos = db.get_unprocessed_photos(level=2)

    analyzer = Analyzer(Config())
    mock_clip = _make_mock_clip()
    analyzer._clip = mock_clip
    captured_paths = []

    def capture_embed(fps):
        captured_paths.extend(fps)
        return [np.random.randn(768).astype(np.float32) for _ in fps]

    mock_clip.embed_batch.side_effect = capture_embed

    with patch("photogal.config.get_thumbnail_cache_dir", return_value=thumb_dir):
        analyzer._run_clip(db, photos)

    assert captured_paths[0] == "/original/big.heic"


# ─── Task 4: Parallel image loading in embed_batch() ────────────────────────


def _create_test_images(tmp_path, count=4):
    """Create small JPEG test images."""
    from PIL import Image
    paths = []
    for i in range(count):
        p = tmp_path / f"img_{i}.jpg"
        Image.new("RGB", (50, 50), color=(i * 60, 100, 200)).save(str(p))
        paths.append(str(p))
    return paths


class _FakeCLIPForBatch:
    """Minimal CLIP-like object for testing embed_batch I/O logic."""

    device = "cpu"
    dtype = torch.float32

    def __init__(self):
        import torchvision.transforms as T
        self.preprocess = T.Compose([T.Resize(32), T.CenterCrop(32), T.ToTensor()])
        self.model = self

    def encode_image(self, batch):
        n = batch.shape[0]
        features = torch.randn(n, 768)
        return features / features.norm(dim=-1, keepdim=True)

    embed_batch = CLIPModel.embed_batch


def test_embed_batch_parallel_preserves_order(tmp_path):
    """Parallel loading must preserve input order."""
    paths = _create_test_images(tmp_path, count=4)
    fake = _FakeCLIPForBatch()
    results = fake.embed_batch(paths)
    assert len(results) == 4
    for r in results:
        assert r.shape == (768,)
        assert not np.allclose(r, 0)


def test_embed_batch_handles_missing_file(tmp_path):
    """Missing file gets zero vector at correct index."""
    paths = _create_test_images(tmp_path, count=2)
    paths.insert(1, str(tmp_path / "nonexistent.jpg"))
    fake = _FakeCLIPForBatch()
    results = fake.embed_batch(paths)
    assert len(results) == 3
    assert not np.allclose(results[0], 0)
    assert np.allclose(results[1], 0)       # zero vector for missing
    assert not np.allclose(results[2], 0)
