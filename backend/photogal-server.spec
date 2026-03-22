# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for photogal-server sidecar binary."""

import importlib.util
import os
import site
import sys

def _find_package_dir(package_name):
    """Find package directory cross-platform."""
    spec = importlib.util.find_spec(package_name)
    if spec and spec.submodule_search_locations:
        return spec.submodule_search_locations[0]
    # Fallback to site-packages
    sp = site.getsitepackages()[0] if site.getsitepackages() else ""
    return os.path.join(sp, package_name)

# Build variant: auto-detect from installed torch, or override via PHOTOGAL_VARIANT.
# - auto (default): checks torch.version.cuda — works on CI without GPU
# - cpu:  force-strip all CUDA binaries
# - cuda: force-keep all CUDA binaries
_variant = os.environ.get("PHOTOGAL_VARIANT", "auto").lower()
if _variant == "auto":
    try:
        import torch as _torch
        _is_cuda = getattr(_torch.version, "cuda", None) is not None
        print(f"[photogal] Auto-detected variant: {'cuda' if _is_cuda else 'cpu'} (torch.version.cuda={getattr(_torch.version, 'cuda', None)})")
    except Exception:
        _is_cuda = False
        print("[photogal] Auto-detected variant: cpu (torch not importable)")
elif _variant == "cuda":
    _is_cuda = True
else:
    _is_cuda = False
print(f"[photogal] Build variant: {'cuda' if _is_cuda else 'cpu'} (PHOTOGAL_VARIANT={_variant})")

# CUDA DLL patterns to strip from CPU builds (Windows)
_CUDA_DLL_PATTERNS = (
    "cuda", "cudnn", "cublas", "cusparse", "cufft", "curand",
    "cusolver", "nccl", "nvrtc", "nvjitlink", "c10_cuda",
    "torch_cuda", "caffe2_nvrtc",
)

a = Analysis(
    ["photogal_entry.py"],
    pathex=["src"],
    binaries=[],
    datas=[
        (os.path.join(_find_package_dir("reverse_geocoder"), "rg_cities1000.csv"), "reverse_geocoder"),
        (os.path.join(_find_package_dir("open_clip"), "bpe_simple_vocab_16e6.txt.gz"), "open_clip"),
        (os.path.join(_find_package_dir("open_clip"), "model_configs"), "open_clip/model_configs"),
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
        "photogal.trash",
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
        # PyTorch
        "torch",
    ] + ([
        # MPS backend only on macOS
        "torch.backends.mps",
    ] if sys.platform == "darwin" else []) + ([
        # CUDA backend only in cuda variant
        "torch.cuda",
        "torch.backends.cuda",
        "torch.backends.cudnn",
    ] if _is_cuda else []) + [
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
    # NOTE: Do NOT exclude torch.cuda / torch.backends.cuda / torch.backends.cudnn.
    # PyTorch's __init__.py imports torch.cuda internally; excluding it causes
    # ModuleNotFoundError at runtime. The Python modules are harmless (torch.cuda
    # just reports is_available()=False). CUDA *native DLLs* are stripped below.
    noarchive=False,
)

# CPU build: strip CUDA native libraries from binaries list
if not _is_cuda:
    _before = len(a.binaries)
    a.binaries = [
        b for b in a.binaries
        if not any(p in b[0].lower() for p in _CUDA_DLL_PATTERNS)
    ]
    _stripped = _before - len(a.binaries)
    if _stripped:
        print(f"[photogal] CPU build: stripped {_stripped} CUDA binaries")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="photogal-server-bin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=(sys.platform != "win32"),
    upx=False,
    console=True,
    **({"target_arch": "arm64"} if sys.platform == "darwin" else {}),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="photogal-server",
)
