"""InsightFace wrapper for face detection + embedding extraction."""

import logging

import insightface
import numpy as np

log = logging.getLogger(__name__)

MIN_FACE_PX = 40  # Skip faces smaller than this (unreliable embeddings)


def _normalize_bbox(
    bbox: np.ndarray, img_width: int, img_height: int,
) -> dict:
    """Convert [x1, y1, x2, y2] pixel coords to normalized {bbox_x, bbox_y, bbox_w, bbox_h}."""
    x1, y1, x2, y2 = bbox.astype(float)
    return {
        "bbox_x": x1 / img_width,
        "bbox_y": y1 / img_height,
        "bbox_w": (x2 - x1) / img_width,
        "bbox_h": (y2 - y1) / img_height,
    }


class FaceModel:
    """Wraps InsightFace buffalo_l for detection + ArcFace embedding."""

    def __init__(self, providers: list[str] | None = None):
        from photogal.device import get_device_info
        if providers is None:
            providers = get_device_info().get_onnx_providers()
        log.info("Loading InsightFace buffalo_l model (providers=%s)...", providers)
        self._app = insightface.app.FaceAnalysis(
            name="buffalo_l",
            providers=providers,
        )
        self._app.prepare(ctx_id=0, det_size=(640, 640))
        log.info("InsightFace model ready on %s.", providers[0])

    def detect(self, img: np.ndarray) -> list[dict]:
        """Detect faces in BGR image array.

        Returns list of dicts with keys:
            bbox: normalized {bbox_x, bbox_y, bbox_w, bbox_h}
            confidence: float
            embedding: np.ndarray (512-dim, L2-normalized)
        """
        h, w = img.shape[:2]
        raw_faces = self._app.get(img)
        results = []
        for f in raw_faces:
            x1, y1, x2, y2 = f.bbox
            face_w_px = x2 - x1
            face_h_px = y2 - y1
            if face_w_px < MIN_FACE_PX or face_h_px < MIN_FACE_PX:
                continue
            if f.normed_embedding is None:
                continue
            norm = _normalize_bbox(f.bbox, img_width=w, img_height=h)
            results.append({
                **norm,
                "confidence": float(f.det_score),
                "embedding": f.normed_embedding.astype(np.float32),
            })
        return results
