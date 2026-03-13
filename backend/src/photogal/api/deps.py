"""FastAPI dependencies."""

import threading

from photogal.db import Database

_db: Database | None = None

_clip = None
_clip_lock = threading.Lock()


def set_db(db: Database):
    global _db
    _db = db


def get_db() -> Database:
    if _db is None:
        raise RuntimeError("DB not initialized")
    return _db


def get_clip():
    """Thread-safe lazy singleton for CLIPModel."""
    global _clip
    if _clip is None:
        with _clip_lock:
            if _clip is None:
                from photogal.config import load_config
                from photogal.models.clip import CLIPModel
                cfg = load_config()
                _clip = CLIPModel(model_name=cfg.clip_model, pretrained=cfg.clip_pretrained)
    return _clip


_face_model = None
_face_model_lock = threading.Lock()


def get_face_model():
    """Thread-safe lazy singleton for FaceModel."""
    global _face_model
    if _face_model is None:
        with _face_model_lock:
            if _face_model is None:
                from photogal.models.face import FaceModel
                _face_model = FaceModel()
    return _face_model
