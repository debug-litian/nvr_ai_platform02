from PyQt5.QtWidgets import QWidget
from PyQt5.QtGui import QImage, QPainter, QFont, QColor
from PyQt5.QtCore import QRect, Qt
import numpy as np


class VideoWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame = None
        self._overlay_backend = ""
        self._overlay_fps = 0.0
        self._overlay_cpu = None
        self._overlay_recv_fps = None
        self._overlay_status = ""

    def set_frame(self, frame: np.ndarray):
        # frame is expected BGR (opencv). Validate before conversion to avoid crashes.
        try:
            import cv2
            import numpy as _np
            if frame is None:
                return
            if not isinstance(frame, _np.ndarray):
                logger = None
                try:
                    from utils.logger import get_logger
                    logger = get_logger("video_widget")
                except Exception:
                    logger = None
                if logger:
                    logger.warning("set_frame received non-ndarray frame: %s", type(frame))
                return
            if frame.size == 0 or frame.ndim < 2:
                return
            # ensure 3 channels
            if frame.ndim == 2:
                rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
            else:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            bytes_per_line = ch * w
            self._qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        except Exception:
            # fallback: try to handle as raw array
            try:
                h, w, ch = frame.shape
                bytes_per_line = ch * w
                self._qimg = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
            except Exception:
                # give up on this frame
                return
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if hasattr(self, "_qimg") and self._qimg is not None:
            rect = QRect(0, 0, self.width(), self.height())
            painter.drawImage(rect, self._qimg.scaled(self.width(), self.height(), Qt.KeepAspectRatio))
        else:
            painter.fillRect(self.rect(), Qt.black)
        # draw overlay text at top-left
        try:
            painter.setRenderHint(QPainter.TextAntialiasing)
            font = QFont("Arial", 10)
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
            if lines:
                text = " | ".join(lines)
                metrics = painter.fontMetrics()
                w = metrics.horizontalAdvance(text) + padding * 2
                h = metrics.height() + padding * 2
                # semi-transparent background
                painter.fillRect(0, 0, w, h, QColor(0, 0, 0, 140))
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(padding, padding + metrics.ascent(), text)
        except Exception:
            pass

    def set_overlay_info(self, backend: str = "", fps: float = 0.0, status: str = "", cpu: float = None, recv_fps: float = None):
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
