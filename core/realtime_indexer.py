import time
from collections import deque
from typing import List, Optional, Dict
import numpy as np
from config import settings
from utils.logger import get_logger
from .feature_extractor import CLIPFeatureExtractor

logger = get_logger("realtime_indexer")

try:
    import faiss
except Exception:
    faiss = None


def _bgr_to_pil(frame):
    from PIL import Image
    import cv2
    if frame is None:
        return None
    if len(frame.shape) == 3 and frame.shape[2] == 3:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)
    if len(frame.shape) == 2:
        return Image.fromarray(frame)
    if len(frame.shape) == 3 and frame.shape[2] == 4:
        rgba = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA)
        return Image.fromarray(rgba)
    return None


class RealtimeIndexer:
    def __init__(self, device: Optional[str] = None, max_items: int = None):
        self.device = device or settings.get_device()
        self.fe = CLIPFeatureExtractor(device=self.device)
        self.max_items = max_items or settings.REALTIME_INDEX_MAX_ITEMS
        self.mapping: List[Dict] = []
        self.vectors: List[np.ndarray] = []
        self.source = "realtime"

    def add_frame(self, frame, ts: float, video_url: str = None, backend: str = ""):
        if self.fe.model is None:
            return
        try:
            pil_image = _bgr_to_pil(frame)
            if pil_image is None:
                return
            emb = self.fe.encode_image(pil_image)
            self.vectors.append(emb)
            self.mapping.append({
                "source": self.source,
                "video": video_url or settings.RTSP_URL,
                "ts": float(ts),
                "backend": backend,
            })
            if len(self.vectors) > self.max_items:
                self.vectors.pop(0)
                self.mapping.pop(0)
        except Exception:
            logger.exception("Failed to index realtime frame")

    def search_text(self, text: str, top_k: int = 10):
        if self.fe.model is None:
            raise RuntimeError("CLIP model not available")
        if len(self.vectors) == 0:
            return []
        q = self.fe.encode_text(text)
        q = q / (np.linalg.norm(q) + 1e-10)
        all_vectors = np.stack(self.vectors).astype("float32")
        # vectors are already normalized by CLIPFeatureExtractor
        scores = np.dot(all_vectors, q.astype("float32"))
        order = np.argsort(-scores)[:top_k]
        results = []
        for idx in order:
            score = float(scores[idx])
            meta = self.mapping[idx]
            results.append({"score": score, "meta": meta})
        return results

    def clear(self):
        self.mapping.clear()
        self.vectors.clear()

    def export_report(self, output_path: str):
        import json
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(self.mapping, f, ensure_ascii=False, indent=2)
            logger.info("Realtime report exported to %s", output_path)
        except Exception:
            logger.exception("Failed to export realtime report")
