"""CLIP model wrapper with aesthetic scoring."""

import logging
from pathlib import Path

import numpy as np
import open_clip
import torch
import torch.nn as nn
from PIL import Image

from photogal.device import get_device_info

logger = logging.getLogger(__name__)

AESTHETIC_URL = (
    "https://github.com/christophschuhmann/improved-aesthetic-predictor"
    "/raw/main/sac+logos+ava1-l14-linearMSE.pth"
)
AESTHETIC_CACHE_PATH = Path.home() / ".cache" / "photoapp" / "aesthetic-predictor-v2.pth"


class _AestheticMLP(nn.Module):
    """MLP head matching LAION aesthetic predictor v2 architecture."""

    def __init__(self):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(768, 1024),
            nn.Dropout(0.2),
            nn.Linear(1024, 128),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.Dropout(0.1),
            nn.Linear(64, 16),
            nn.Linear(16, 1),
        )

    def forward(self, x):
        return self.layers(x)


class CLIPModel:
    """CLIP ViT-L/14 wrapper with LAION aesthetic predictor v2."""

    @staticmethod
    def is_model_cached(model_name: str = "ViT-L-14", pretrained: str = "laion2b_s32b_b82k") -> bool:
        """Check if CLIP model weights are already downloaded."""
        try:
            from open_clip.pretrained import get_pretrained_cfg
            cfg = get_pretrained_cfg(model_name, pretrained)
            if not cfg:
                return True
            hf_hub = cfg.get('hf_hub')
            if not hf_hub:
                return True
            from huggingface_hub import try_to_load_from_cache
            result = try_to_load_from_cache(hf_hub.rstrip('/'), "open_clip_pytorch_model.bin")
            return isinstance(result, str)
        except Exception:
            return True  # assume cached on errors

    def __init__(
        self,
        model_name: str = "ViT-L-14",
        pretrained: str = "laion2b_s32b_b82k",
        device: str | None = None,
    ):
        if device is None:
            info = get_device_info()
            if info.gpu_validated is None:
                from photogal.device import validate_gpu
                validate_gpu(info)
            self.device = info.backend
            self.dtype = info.dtype
        else:
            self.device = device
            self.dtype = torch.float16 if device in ("cuda", "mps") else torch.float32

        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass

        self.model_name = model_name
        logger.info("Loading CLIP model %s (%s) on %s", model_name, pretrained, self.device)
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained, device=self.device
        )
        self.model.eval()
        self.model = self.model.to(dtype=self.dtype)

        self.aesthetic_head = self._load_aesthetic_head()

    def _load_aesthetic_head(self) -> nn.Module:
        """Load LAION aesthetic predictor v2 (MLP head on CLIP embeddings)."""
        if not AESTHETIC_CACHE_PATH.exists():
            logger.info("Downloading aesthetic predictor to %s", AESTHETIC_CACHE_PATH)
            AESTHETIC_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            import urllib.request

            urllib.request.urlretrieve(AESTHETIC_URL, AESTHETIC_CACHE_PATH)

        head = _AestheticMLP()
        state_dict = torch.load(AESTHETIC_CACHE_PATH, map_location=self.device, weights_only=True)
        head.load_state_dict(state_dict)
        head.to(self.device)
        head.to(dtype=self.dtype)
        head.eval()
        return head

    def embed_image(self, filepath: str) -> np.ndarray:
        """Return L2-normalized 768-dim float32 embedding for an image."""
        img = Image.open(filepath).convert("RGB")
        tensor = self.preprocess(img).unsqueeze(0).to(self.device, dtype=self.dtype)
        with torch.inference_mode():
            if self.device == "cuda":
                with torch.amp.autocast("cuda", dtype=torch.float16):
                    features = self.model.encode_image(tensor)
            else:
                features = self.model.encode_image(tensor)
            features = features / features.norm(dim=-1, keepdim=True)
        return features.squeeze(0).cpu().numpy().astype(np.float32)

    def embed_batch(self, filepaths: list[str]) -> list[np.ndarray]:
        """Batch embed images. Returns zero vector for any failures."""
        from concurrent.futures import ThreadPoolExecutor

        def _load_one(idx_fp):
            i, fp = idx_fp
            try:
                img = Image.open(fp).convert("RGB")
                return i, self.preprocess(img)
            except Exception:
                logger.warning("Failed to load %s, using zero vector", fp)
                return i, None

        tensors = []
        valid_indices = []
        with ThreadPoolExecutor(max_workers=min(len(filepaths), 8)) as executor:
            for i, tensor in executor.map(_load_one, enumerate(filepaths)):
                if tensor is not None:
                    tensors.append(tensor)
                    valid_indices.append(i)

        if not tensors:
            return [np.zeros(768, dtype=np.float32)] * len(filepaths)

        batch = torch.stack(tensors).to(self.device, dtype=self.dtype)
        with torch.inference_mode():
            if self.device == "cuda":
                with torch.amp.autocast("cuda", dtype=torch.float16):
                    features = self.model.encode_image(batch)
            else:
                features = self.model.encode_image(batch)
            features = features / features.norm(dim=-1, keepdim=True)
        embeddings_np = features.cpu().numpy().astype(np.float32)

        results = [np.zeros(768, dtype=np.float32)] * len(filepaths)
        for idx, orig_idx in enumerate(valid_indices):
            results[orig_idx] = embeddings_np[idx]
        return results

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Return L2-normalized text embeddings as (N, 768) float32 array."""
        tokenizer = open_clip.get_tokenizer(self.model_name)
        tokens = tokenizer(texts).to(self.device)
        with torch.inference_mode():
            if self.device == "cuda":
                with torch.amp.autocast("cuda", dtype=torch.float16):
                    features = self.model.encode_text(tokens)
            else:
                features = self.model.encode_text(tokens)
            features = features / features.norm(dim=-1, keepdim=True)
        return features.cpu().numpy().astype(np.float32)

    def aesthetic_score(self, filepath: str) -> float:
        """Return aesthetic score (clamped to 1.0-10.0) for an image."""
        emb = self.embed_image(filepath)
        return self.aesthetic_score_from_embedding(emb)

    def aesthetic_score_from_embedding(self, embedding: np.ndarray) -> float:
        """Return aesthetic score from a precomputed CLIP embedding."""
        tensor = torch.from_numpy(embedding).unsqueeze(0).to(self.device, dtype=self.dtype)
        with torch.inference_mode():
            raw = self.aesthetic_head(tensor).item()
        return float(np.clip(raw, 1.0, 10.0))

    def aesthetic_scores_batch(self, embeddings: list[np.ndarray]) -> list[float]:
        """Batch aesthetic scoring -- single MLP forward pass."""
        if not embeddings:
            return []
        stacked = torch.from_numpy(np.stack(embeddings)).to(self.device, dtype=self.dtype)
        with torch.inference_mode():
            raw = self.aesthetic_head(stacked).squeeze(-1)
        return [float(np.clip(v, 1.0, 10.0)) for v in raw.cpu().numpy()]

    def unload(self):
        """Release model from memory."""
        del self.model
        del self.aesthetic_head
        if self.device == "cuda":
            torch.cuda.empty_cache()
        elif self.device == "mps":
            torch.mps.empty_cache()
        logger.info("CLIP model unloaded")
