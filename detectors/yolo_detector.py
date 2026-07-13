from typing import List
import threading
import numpy as np
from utils.logger import get_logger
from config import settings

logger = get_logger("yolo_detector")

try:
    from ultralytics import YOLO
except Exception:
    YOLO = None


class YoloDetector:
    def __init__(self, model_path: str = None, device: str = None):
        self.model_path = model_path or str(settings.YOLO_MODEL_DIR / "yolov8n.pt")
        self.device = device or settings.get_device()
        self.model = None
        self._lock = threading.Lock()  # 线程安全锁（FTP核验 + 预览可能同时调用）
        self._load()

    def _load(self):
        if YOLO is None:
            logger.warning("ultralytics YOLO not installed")
            return
        try:
            self.model = YOLO(self.model_path)
            logger.info("YOLO model loaded: %s", self.model_path)
        except Exception:
            logger.exception("Failed to load YOLO model")

    def detect(self, frame: np.ndarray, conf: float = None, verbose: bool = False) -> List[List[float]]:
        if self.model is None:
            return []
        conf = conf or settings.YOLO_CONFIDENCE_THRESHOLD
        try:
            with self._lock:
                results = self.model.predict(frame, imgsz=640, conf=conf, verbose=verbose)
            out = []
            for r in results:
                boxes = r.boxes
                for b in boxes:
                    xyxy = b.xyxy[0].cpu().numpy().tolist()
                    score = float(b.conf[0].cpu().numpy())
                    cls = int(b.cls[0].cpu().numpy())
                    out.append([xyxy[0], xyxy[1], xyxy[2], xyxy[3], score, cls])
            return out
        except Exception:
            logger.exception("YOLO detection error")
            return []
