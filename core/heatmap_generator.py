"""
heatmap_generator.py — 运动热力图生成器

原理：
- 帧间差分：当前帧 vs 上一帧的灰度图，变化 >threshold 的像素记为"运动"
- 热力矩阵累积：运动像素 +1，静止像素缓慢衰减（decay）
- 伪彩色映射：OpenCV COLORMAP_JET（蓝→绿→黄→红：冷→热）
- 半透明叠加：cv2.addWeighted 将热力图叠在原画面上

用途：
- 验证 NVR 自带热力图的准确性
- 分析画面中哪些区域运动频率最高（人流/车流分析）

非 QThread，纯计算逻辑，由主线程 _refresh_display() 中调用。
"""

import time
import numpy as np
import cv2
from typing import Optional, List, Tuple
from utils.logger import get_logger

logger = get_logger("heatmap_generator")


class HeatmapGenerator:
    """
    运动热力图生成器。

    参数:
        width, height: 热力矩阵尺寸（应与输入帧尺寸一致）
        decay:          静止像素衰减速率（每帧减多少）
        threshold:      帧间差分二值化阈值
        max_value:      热力值上限
    """

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        decay: float = 0.15,
        threshold: int = 28,
        max_value: float = 255.0,
    ):
        self.heatmap = np.zeros((height, width), dtype=np.float32)
        self.prev_gray: Optional[np.ndarray] = None
        self.decay = decay
        self.threshold = threshold
        self.max_value = max_value

        # 统计
        self._frame_count: int = 0
        self._start_time: float = time.time()
        self._last_resize_warning: bool = False

    # ── 公共接口 ──────────────────────────────────────

    def update(self, frame: np.ndarray) -> np.ndarray:
        """
        输入当前帧 (BGR)，更新内部热力矩阵，返回伪彩色热力图。

        返回:
            BGR uint8 格式的伪彩色热力图，尺寸与输入帧一致。
        """
        if frame is None or frame.size == 0:
            return np.zeros((self.heatmap.shape[0], self.heatmap.shape[1], 3), dtype=np.uint8)

        # 转为灰度
        if frame.ndim == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame

        # 尺寸自适应
        h, w = gray.shape
        if h != self.heatmap.shape[0] or w != self.heatmap.shape[1]:
            if not self._last_resize_warning:
                logger.info("热力图矩阵自适应: %dx%d → %dx%d", self.heatmap.shape[1], self.heatmap.shape[0], w, h)
                self._last_resize_warning = True
            self.heatmap = np.zeros((h, w), dtype=np.float32)
            self.prev_gray = None

        # 帧间差分
        if self.prev_gray is not None and self.prev_gray.shape == gray.shape:
            # 计算绝对差
            diff = cv2.absdiff(gray, self.prev_gray)

            # 高斯模糊减少噪点
            diff = cv2.GaussianBlur(diff, (5, 5), 0)

            # 二值化：运动区域=1，静止=0
            _, motion = cv2.threshold(diff, self.threshold, 1, cv2.THRESH_BINARY)

            # 形态学开运算：去除孤立噪点
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            motion = cv2.morphologyEx(motion.astype(np.uint8), cv2.MORPH_OPEN, kernel)

            # 累积热力
            self.heatmap += motion.astype(np.float32)

            # 全局衰减
            self.heatmap -= self.decay
            self.heatmap = np.clip(self.heatmap, 0, self.max_value)

        self.prev_gray = gray.copy()
        self._frame_count += 1

        # 归一化 → 伪彩色
        return self.get_colored(self.heatmap)

    def get_colored(self, heat_matrix: Optional[np.ndarray] = None) -> np.ndarray:
        """
        将热力矩阵转为伪彩色 BGR 图像 (JET: 蓝→绿→黄→红)。
        """
        if heat_matrix is None:
            heat_matrix = self.heatmap

        if heat_matrix.max() > 0:
            heat_8u = (heat_matrix / heat_matrix.max() * 255).astype(np.uint8)
        else:
            heat_8u = heat_matrix.astype(np.uint8)

        return cv2.applyColorMap(heat_8u, cv2.COLORMAP_JET)

    def overlay(self, frame: np.ndarray, alpha: float = 0.30) -> np.ndarray:
        """
        一站式方法：更新热力矩阵 + 叠加到原帧上并返回。

        Args:
            frame: BGR 原始帧
            alpha: 热力图叠加透明度 (0=不可见, 1=完全覆盖)

        Returns:
            BGR 帧 + 半透明热力图叠加
        """
        heat = self.update(frame)

        # 确保尺寸匹配
        if heat.shape[:2] != frame.shape[:2]:
            heat = cv2.resize(heat, (frame.shape[1], frame.shape[0]))

        result = cv2.addWeighted(frame, 1.0 - alpha, heat, alpha, 0)
        return result

    def reset(self):
        """重置热力矩阵和统计信息"""
        h, w = self.heatmap.shape
        self.heatmap.fill(0)
        self.prev_gray = None
        self._frame_count = 0
        self._start_time = time.time()

    # ── 峰值区域分析 ─────────────────────────────────

    def get_peak_regions(
        self, top_n: int = 5, min_distance: int = 30
    ) -> List[Tuple[int, int, float]]:
        """
        返回热力值最高的 N 个区域（去重，非极大值抑制）。

        Returns:
            [(y_center, x_center, heat_value), ...] 按热力值降序
        """
        if self.heatmap.max() <= 0:
            return []

        # 膨胀使峰值区域合并
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (min_distance, min_distance))
        dilated = cv2.dilate(self.heatmap, kernel)
        max_mask = (self.heatmap == dilated) & (self.heatmap > 0)

        if not np.any(max_mask):
            return []

        ys, xs = np.where(max_mask)
        values = self.heatmap[max_mask]
        sorted_idx = np.argsort(-values)

        peaks = []
        used_positions = []

        for idx in sorted_idx:
            y = int(ys[idx])
            x = int(xs[idx])
            val = float(values[idx])

            # 非极大值抑制
            too_close = False
            for uy, ux in used_positions:
                if abs(y - uy) < min_distance and abs(x - ux) < min_distance:
                    too_close = True
                    break

            if not too_close:
                peaks.append((y, x, val))
                used_positions.append((y, x))

            if len(peaks) >= top_n:
                break

        return peaks

    def get_peak_rects(self, top_n=5, rect_size=40) -> List[Tuple[int, int, int, int]]:
        """
        返回峰值区域的矩形框（用于在画面上标注）。

        Returns:
            [(x1, y1, x2, y2), ...]  按热力值降序排列
        """
        peaks = self.get_peak_regions(top_n)
        rects = []
        for y, x, val in peaks:
            half = rect_size // 2
            x1 = max(0, x - half)
            y1 = max(0, y - half)
            x2 = min(self.heatmap.shape[1], x + half)
            y2 = min(self.heatmap.shape[0], y + half)
            rects.append((x1, y1, x2, y2))
        return rects

    # ── 统计 ─────────────────────────────────────────

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self._start_time

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def mean_heat(self) -> float:
        """全局平均热力值"""
        return float(np.mean(self.heatmap))

    @property
    def max_heat(self) -> float:
        """最高热力值"""
        return float(np.max(self.heatmap))

    def get_heat_stats(self) -> dict:
        """返回热力图统计摘要"""
        return {
            "elapsed_sec": round(self.elapsed_seconds, 1),
            "frame_count": self.frame_count,
            "mean_heat": round(self.mean_heat, 2),
            "max_heat": round(self.max_heat, 2),
            "peak_regions": len(self.get_peak_regions(3)),
            "matrix_shape": list(self.heatmap.shape),
        }
