import json
import numpy as np
import cv2
from pathlib import Path
from typing import List, Tuple
from config import settings
from utils.logger import get_logger
from .feature_extractor import CLIPFeatureExtractor

logger = get_logger("index_builder")

try:
    import faiss
except Exception:
    faiss = None


class IndexBuilder:
    def __init__(self, dim=512, index_path: Path = None, mapping_path: Path = None):
        self.dim = dim
        self.index_path = index_path or settings.INDEX_FILE
        self.mapping_path = mapping_path or settings.MAPPING_FILE
        self.index = None
        self.mapping = []
        self._load_or_create()

    def _load_or_create(self):
        if faiss is None:
            logger.warning("faiss not available")
            return
        if self.index_path.exists():
            try:
                self.index = faiss.read_index(str(self.index_path))
                logger.info("Loaded FAISS index from %s", self.index_path)
            except Exception:
                logger.exception("Failed to load index, creating new")
        if self.index is None:
            quantizer = faiss.IndexFlatL2(self.dim)
            self.index = faiss.IndexFlatL2(self.dim)

        if Path(self.mapping_path).exists():
            try:
                with open(self.mapping_path, "r", encoding="utf-8") as f:
                    self.mapping = json.load(f)
            except Exception:
                logger.exception("Failed to load mapping file")

    def add(self, vectors: List[np.ndarray], metas: List[dict]):
        if faiss is None:
            raise RuntimeError("faiss not installed")
        if len(vectors) == 0:
            return
        arr = np.stack(vectors).astype("float32")
        self.index.add(arr)
        self.mapping.extend(metas)

    def build_from_video(self, video_path: str, sample_fps: float = None, max_frames: int = None):
        if self.index is None:
            raise RuntimeError("faiss index not available")
        if self.index_path is None or self.mapping_path is None:
            raise RuntimeError("Index or mapping path not configured")
        if sample_fps is None:
            sample_fps = settings.VIDEO_INDEX_SAMPLE_FPS
        if max_frames is None:
            max_frames = 1000

        fe = CLIPFeatureExtractor(device=settings.get_device())
        if fe.model is None:
            raise RuntimeError("CLIP model not available")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Failed to open video for indexing: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        interval = 1
        if fps > 0 and sample_fps > 0:
            interval = max(1, int(round(fps / sample_fps)))

        frame_count = 0
        hashed = 0
        vectors = []
        metas = []
        import cv2 as _cv2
        from PIL import Image
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_count += 1
            if frame_count % interval != 0:
                continue
            if max_frames and len(vectors) >= max_frames:
                break
            try:
                pil_image = Image.fromarray(_cv2.cvtColor(frame, _cv2.COLOR_BGR2RGB))
                emb = fe.encode_image(pil_image)
                vectors.append(emb)
                ts = frame_count / fps if fps > 0 else 0.0
                metas.append({
                    "source": "video",
                    "video": str(video_path),
                    "ts": float(ts),
                    "frame_index": frame_count,
                })
                hashed += 1
            except Exception:
                logger.exception("Frame encoding failed during video indexing")
                continue
        cap.release()

        self.add(vectors, metas)
        self.save()
        logger.info("Built video index from %s, frames=%d", video_path, len(vectors))

    def save(self):
        if faiss is None:
            return
        try:
            faiss.write_index(self.index, str(self.index_path))
            with open(self.mapping_path, "w", encoding="utf-8") as f:
                json.dump(self.mapping, f, ensure_ascii=False)
            logger.info("Saved index and mapping")
        except Exception:
            logger.exception("Failed to save index/mapping")
