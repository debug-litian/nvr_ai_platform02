"""
VideoWidget — 视频显示组件（支持滚轮缩放 + 鼠标拖拽平移）

特性：
- 鼠标滚轮：放大/缩小画面（0.25x ~ 4.0x）
- 鼠标右键拖拽：平移画面
- 双击：重置缩放和位置
- 显示 YOLO 检测框（画在 frame 上的 OpenCV 框）
- 顶部叠加 OSD 信息（Backend / FPS / RecvFPS / CPU / Status）
"""
from PyQt5.QtWidgets import QWidget
from PyQt5.QtGui import QImage, QPainter, QFont, QColor
from PyQt5.QtCore import QRect, Qt, QPointF
import numpy as np


class VideoWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame = None

        # ── OSD 信息 ────────────────────────────────────
        self._overlay_backend = ""
        self._overlay_fps = 0.0
        self._overlay_cpu = None
        self._overlay_recv_fps = None
        self._overlay_status = ""

        # ── 缩放与平移 ──────────────────────────────────
        self._zoom = 1.0           # 缩放倍数
        self._min_zoom = 0.25
        self._max_zoom = 4.0
        self._offset_x = 0.0       # 图像中心相对于窗口中心的偏移（像素）
        self._offset_y = 0.0
        self._last_mouse_pos = None  # 拖拽时上一次鼠标位置

        # 启用鼠标追踪（不按按钮也能接收 move 事件）
        self.setMouseTracking(True)

    # ═══════════════════════════════════════════════════════
    # 帧设置
    # ═══════════════════════════════════════════════════════

    def set_frame(self, frame: np.ndarray):
        """设置当前帧（BGR 格式），转换为内部 QImage"""
        try:
            import cv2
            if frame is None:
                return
            if not isinstance(frame, np.ndarray):
                return
            if frame.size == 0 or frame.ndim < 2:
                return
            # BGR → RGB
            if frame.ndim == 2:
                rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
            else:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            bytes_per_line = ch * w
            self._qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        except Exception:
            try:
                h, w, ch = frame.shape
                bytes_per_line = ch * w
                self._qimg = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
            except Exception:
                return
        self.update()

    # ═══════════════════════════════════════════════════════
    # OSD 叠加信息
    # ═══════════════════════════════════════════════════════

    def set_overlay_info(
        self,
        backend: str = "",
        fps: float = 0.0,
        status: str = "",
        cpu: float = None,
        recv_fps: float = None,
    ):
        self._overlay_backend = backend or ""
        try:
            self._overlay_fps = float(fps or 0.0)
        except Exception:
            self._overlay_fps = 0.0
        self._overlay_status = status or ""
        try:
            self._overlay_cpu = None if cpu is None else float(cpu)
        except Exception:
            self._overlay_cpu = None
        try:
            self._overlay_recv_fps = None if recv_fps is None else float(recv_fps)
        except Exception:
            self._overlay_recv_fps = None
        self.update()

    # ═══════════════════════════════════════════════════════
    # 绘制
    # ═══════════════════════════════════════════════════════

    def _get_scaled_rect(self):
        """
        计算缩放后图像在 widget 中的绘制矩形。
        返回 (source_rect在widget中的位置, 实际缩放比)
        """
        if not hasattr(self, "_qimg") or self._qimg is None:
            return None, 1.0

        iw = self._qimg.width()
        ih = self._qimg.height()
        ww = self.width()
        wh = self.height()

        if iw <= 0 or ih <= 0 or ww <= 0 or wh <= 0:
            return None, 1.0

        # 按 widget 大小适配的基础缩放比
        base = min(ww / iw, wh / ih)
        scale = base * self._zoom

        # 缩放后图像尺寸
        sw = int(iw * scale)
        sh = int(ih * scale)

        # 居中 + 偏移
        cx = (ww - sw) // 2 + int(self._offset_x)
        cy = (wh - sh) // 2 + int(self._offset_y)

        return QRect(cx, cy, sw, sh), scale

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        if not hasattr(self, "_qimg") or self._qimg is None:
            painter.fillRect(self.rect(), Qt.black)
            self._draw_osd(painter)
            return

        target_rect, _ = self._get_scaled_rect()
        if target_rect is None:
            painter.fillRect(self.rect(), Qt.black)
            self._draw_osd(painter)
            return

        # 绘制缩放后的图像
        painter.drawImage(target_rect, self._qimg)

        # OSD 文字
        self._draw_osd(painter)

    def _draw_osd(self, painter: QPainter):
        """绘制左上角 OSD 叠加信息"""
        try:
            painter.setRenderHint(QPainter.TextAntialiasing)
            font = QFont("Consolas", 10)
            painter.setFont(font)
            padding = 6
            lines = []
            if self._overlay_backend:
                lines.append(f"Backend: {self._overlay_backend}")
            if self._overlay_fps is not None:
                lines.append(f"FPS: {self._overlay_fps:.1f}")
            if self._overlay_recv_fps is not None:
                lines.append(f"RecvFPS: {self._overlay_recv_fps:.1f}")
            if self._overlay_cpu is not None:
                lines.append(f"CPU: {self._overlay_cpu:.0f}%")
            if self._overlay_status:
                lines.append(f"Status: {self._overlay_status}")
            zoom_pct = int(self._zoom * 100)
            lines.append(f"Zoom: {zoom_pct}%")
            if lines:
                text = " | ".join(lines)
                metrics = painter.fontMetrics()
                w = metrics.horizontalAdvance(text) + padding * 2
                h = metrics.height() * len(lines) + padding * 2
                # 半透明背景
                painter.fillRect(0, 0, w, h, QColor(0, 0, 0, 150))
                painter.setPen(QColor(255, 255, 255))
                for i, line in enumerate(lines):
                    y = padding + metrics.ascent() + i * metrics.height()
                    painter.drawText(padding, y, line)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════
    # 鼠标事件 — 滚轮缩放 + 右键拖拽 + 双击重置
    # ═══════════════════════════════════════════════════════

    def wheelEvent(self, event):
        """滚轮缩放"""
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom = min(self._max_zoom, self._zoom * 1.15)
        else:
            self._zoom = max(self._min_zoom, self._zoom / 1.15)
        self.update()

    def mousePressEvent(self, event):
        """右键按下 → 开始拖拽"""
        if event.button() == Qt.RightButton:
            self._last_mouse_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.LeftButton:
            # 双击检测
            pass

    def mouseMoveEvent(self, event):
        """右键拖拽平移"""
        if self._last_mouse_pos is not None:
            pos = event.pos()
            self._offset_x += pos.x() - self._last_mouse_pos.x()
            self._offset_y += pos.y() - self._last_mouse_pos.y()
            self._last_mouse_pos = pos
            self.update()

    def mouseReleaseEvent(self, event):
        """右键松开 → 停止拖拽"""
        if event.button() == Qt.RightButton:
            self._last_mouse_pos = None
            self.setCursor(Qt.ArrowCursor)

    def mouseDoubleClickEvent(self, event):
        """双击 → 重置缩放和位置"""
        if event.button() == Qt.LeftButton:
            self._zoom = 1.0
            self._offset_x = 0.0
            self._offset_y = 0.0
            self.update()
