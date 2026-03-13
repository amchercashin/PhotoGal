"""Tests for FaceModel wrapper (unit tests with mocked model)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
from unittest.mock import MagicMock, patch


def test_face_model_init_and_detect():
    """Test that FaceModel wraps InsightFace correctly."""
    with patch("photogal.models.face.insightface") as mock_if:
        mock_app = MagicMock()
        mock_if.app.FaceAnalysis.return_value = mock_app

        # Mock a detected face
        mock_face = MagicMock()
        mock_face.bbox = np.array([100, 50, 200, 200])  # x1, y1, x2, y2
        mock_face.det_score = 0.95
        mock_face.normed_embedding = np.random.randn(512).astype(np.float32)
        mock_app.get.return_value = [mock_face]

        from photogal.models.face import FaceModel
        model = FaceModel()

        img = np.zeros((400, 600, 3), dtype=np.uint8)
        faces = model.detect(img)

        assert len(faces) == 1
        f = faces[0]
        assert "bbox_x" in f
        assert "confidence" in f
        assert "embedding" in f
        assert f["embedding"].shape == (512,)
        assert f["confidence"] == 0.95


def test_normalize_bbox():
    """Test bbox normalization to 0.0-1.0 range."""
    from photogal.models.face import _normalize_bbox
    # Image 600x400, face bbox [100, 50, 200, 200] (x1,y1,x2,y2)
    result = _normalize_bbox(
        np.array([100, 50, 200, 200]),
        img_width=600, img_height=400,
    )
    assert abs(result["bbox_x"] - 100/600) < 1e-5
    assert abs(result["bbox_y"] - 50/400) < 1e-5
    assert abs(result["bbox_w"] - 100/600) < 1e-5
    assert abs(result["bbox_h"] - 150/400) < 1e-5


def test_face_model_no_faces():
    """Empty image returns no faces."""
    with patch("photogal.models.face.insightface") as mock_if:
        mock_app = MagicMock()
        mock_if.app.FaceAnalysis.return_value = mock_app
        mock_app.get.return_value = []

        from photogal.models.face import FaceModel
        model = FaceModel()
        faces = model.detect(np.zeros((400, 600, 3), dtype=np.uint8))
        assert faces == []
