import cv2
import numpy as np
from typing import Tuple, Dict
from utils.logger import get_logger
from config import settings

logger = get_logger("green_line_detector")


def detect_green_and_vertical_lines(frame: np.ndarray, threshold: float = None) -> Dict:
    if threshold is None:
        threshold = settings.GREEN_LINE_THRESHOLD
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower = np.array([35, 50, 50])
    upper = np.array([85, 255, 255])
    mask = cv2.inRange(hsv, lower, upper)
    green_ratio = float(np.count_nonzero(mask) / (mask.size + 1e-10))

    # visualize mask (BGR)
    vis_mask = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)

    # detect vertical lines using Hough
    edges = cv2.Canny(mask, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=50, minLineLength=50, maxLineGap=10)
    vertical_lines = []
    if lines is not None:
        for x1, y1, x2, y2 in lines.reshape(-1, 4):
            dx = x2 - x1
            dy = y2 - y1
            if abs(dx) < abs(dy) * 0.3:  # mostly vertical
                vertical_lines.append((x1, y1, x2, y2))
                cv2.line(vis_mask, (x1, y1), (x2, y2), (0, 0, 255), 2)

    abnormal = green_ratio > threshold or len(vertical_lines) > 0
    return {
        "abnormal": bool(abnormal),
        "green_ratio": green_ratio,
        "mask_vis": vis_mask,
        "vertical_lines": vertical_lines,
    }
