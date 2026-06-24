import json
import numpy as np
from pathlib import Path
from typing import List, Tuple
from config import settings
from utils.logger import get_logger

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
