# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for photogal-server sidecar binary."""

import os
import site

# Find site-packages for data files
sp = site.getsitepackages()[0] if site.getsitepackages() else os.path.join(
    os.path.dirname(os.path.abspath(".")), ".venv", "lib", "python3.12", "site-packages"
)

a = Analysis(
    ["photogal_entry.py"],
    pathex=["src"],
    binaries=[],
    datas=[
        # reverse_geocoder needs its CSV data
        (os.path.join(sp, "reverse_geocoder", "rg_cities1000.csv"), "reverse_geocoder"),
        # open_clip needs BPE vocab and model configs
        (os.path.join(sp, "open_clip", "bpe_simple_vocab_16e6.txt.gz"), "open_clip"),
        (os.path.join(sp, "open_clip", "model_configs"), "open_clip/model_configs"),
    ],
    hiddenimports=[
        # photogal modules (lazy-imported)
        "photogal.server",
        "photogal.config",
        "photogal.db",
        "photogal.search",
        "photogal.thumbnails",
        "photogal.api.deps",
        "photogal.api.photos",
        "photogal.api.clusters",
        "photogal.api.sources",
        "photogal.api.process",
        "photogal.api.sync",
        "photogal.api.search",
        "photogal.pipeline.scanner",
        "photogal.pipeline.analyzer",
        "photogal.pipeline.clusterer",
        "photogal.pipeline.embedder",
        "photogal.pipeline.helpers",
        "photogal.models.clip",
        "photogal.models.face",
        "photogal.pipeline.face_analyzer",
        "photogal.api.persons",
        "photogal.api.faces",
        "photogal.api.device",
        "photogal.device",
        "photogal.translate",
        "photogal.profiling",
        # InsightFace + ONNX
        "insightface",
        "insightface.app",
        "insightface.app.face_analysis",
        "insightface.model_zoo",
        "insightface.model_zoo.model_zoo",
        "insightface.model_zoo.arcface_onnx",
        "insightface.model_zoo.retinaface",
        "insightface.model_zoo.scrfd",
        "insightface.model_zoo.landmark",
        "insightface.model_zoo.attribute",
        "insightface.utils",
        "insightface.utils.face_align",
        "insightface.utils.transform",
        "onnxruntime",
        "albumentations",
        "cv2",
        # uvicorn internals
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # FastAPI / Starlette
        "starlette.responses",
        "starlette.staticfiles",
        "starlette.middleware.cors",
        "multipart",
        "multipart.multipart",
        # Libraries
        "PIL",
        "pillow_heif",
        "exifread",
        "imagehash",
        "sklearn.cluster",
        "sklearn.neighbors",
        "sklearn.utils._typedefs",
        "scipy.spatial",
        "scipy.spatial.ckdtree",
        # PyTorch — ensure MPS backend is included on macOS
        "torch",
        "torch.backends.mps",
        # argos-translate (offline ru→en translation)
        "argostranslate",
        "argostranslate.package",
        "argostranslate.translate",
        "ctranslate2",
        "sentencepiece",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude unnecessary large modules to reduce binary size
        "tkinter",
        "matplotlib",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
        "setuptools",
        "pip",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="photogal-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,  # UPX breaks some torch binaries on macOS
    console=True,
    target_arch="arm64",
)
