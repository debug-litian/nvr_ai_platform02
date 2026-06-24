import numpy as np
from config import settings
from utils.logger import get_logger

logger = get_logger("searcher")

try:
    import faiss
except Exception:
    faiss = None

from .feature_extractor import CLIPFeatureExtractor


class Searcher:
    def __init__(self, device=None, index_path=None, mapping_path=None):
        self.device = device or settings.get_device()
        self.fe = CLIPFeatureExtractor(device=self.device)
        self.index_path = index_path or settings.INDEX_FILE
        self.mapping_path = mapping_path or settings.MAPPING_FILE
        self.index = None
        self.mapping = []
        self._load()

    def _load(self):
        if faiss is None:
            logger.warning("faiss not available")
            return
        try:
            if self.index_path.exists():
                self.index = faiss.read_index(str(self.index_path))
            if self.mapping_path.exists():
                import json
                with open(self.mapping_path, "r", encoding="utf-8") as f:
                    self.mapping = json.load(f)
        except Exception:
            logger.exception("Failed to load searcher data")

    def search_text(self, text: str, top_k: int = None):
        if self.index is None:
            raise RuntimeError("Index not loaded")
        top_k = top_k or settings.TOP_K
        q = self.fe.encode_text(text)
        q = np.expand_dims(q, axis=0).astype("float32")
        D, I = self.index.search(q, top_k)
        results = []
        for dist, idx in zip(D[0], I[0]):
            if idx < 0 or idx >= len(self.mapping):
                continue
            meta = self.mapping[idx]
            results.append({"score": float(1.0 / (1.0 + dist)), "meta": meta})
        return results
