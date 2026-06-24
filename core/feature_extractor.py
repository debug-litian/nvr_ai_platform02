import torch
import numpy as np
from typing import Tuple
from config import settings
from utils.logger import get_logger

logger = get_logger("feature_extractor")

try:
    import clip
    from PIL import Image
except Exception:
    clip = None


class CLIPFeatureExtractor:
    def __init__(self, device: str = None):
        self.device = device or settings.get_device()
        self.model = None
        self.preprocess = None
        self._load()

    def _load(self):
        if clip is None:
            logger.warning("CLIP not installed")
            return
        try:
            self.model, self.preprocess = clip.load("ViT-B/32", device=self.device)
            self.model.eval()
            logger.info("CLIP loaded on %s", self.device)
        except Exception as e:
            logger.exception("Failed to load CLIP: %s", e)

    def encode_image(self, pil_image) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("CLIP model not available")
        with torch.no_grad():
            image = self.preprocess(pil_image).unsqueeze(0).to(self.device)
            emb = self.model.encode_image(image)
            emb = emb.cpu().numpy()[0]
            emb = emb / (np.linalg.norm(emb) + 1e-10)
            return emb.astype("float32")

    def encode_text(self, text: str) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("CLIP model not available")
        with torch.no_grad():
            tokens = clip.tokenize([text]).to(self.device)
            emb = self.model.encode_text(tokens)
            emb = emb.cpu().numpy()[0]
            emb = emb / (np.linalg.norm(emb) + 1e-10)
            return emb.astype("float32")
