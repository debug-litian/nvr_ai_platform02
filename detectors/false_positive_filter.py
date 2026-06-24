import time
from typing import List, Dict, Tuple
import numpy as np
from utils.logger import get_logger
from config import settings

logger = get_logger("false_positive_filter")


class FalsePositiveFilter:
    def __init__(self, conf_threshold: float = None, area_threshold: int = 1000, max_history=5):
        self.conf_threshold = conf_threshold or settings.YOLO_CONFIDENCE_THRESHOLD
        self.area_threshold = area_threshold
        self.history = []
        self.max_history = max_history

    def _area(self, box: List[float]) -> float:
        x1, y1, x2, y2 = box[:4]
        return max(0.0, (x2 - x1) * (y2 - y1))

    def filter(self, detections: List[List[float]]) -> List[List[float]]:
        # detections: [x1,y1,x2,y2,conf,class]
        ok = []
        for d in detections:
            area = self._area(d)
            conf = float(d[4])
            if conf < self.conf_threshold:
                continue
            if area < self.area_threshold:
                continue
            ok.append(d)

        # simple motion consistency: compare with last frame
        if self.history:
            prev = self.history[-1]
            final = []
            for a in ok:
                ax = (a[0] + a[2]) / 2
                ay = (a[1] + a[3]) / 2
                matched = False
                for b in prev:
                    bx = (b[0] + b[2]) / 2
                    by = (b[1] + b[3]) / 2
                    if abs(ax - bx) < 50 and abs(ay - by) < 50:
                        matched = True
                        break
                if matched:
                    final.append(a)
            ok = final

        self.history.append(ok)
        if len(self.history) > self.max_history:
            self.history.pop(0)
        return ok
