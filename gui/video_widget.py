from PyQt5.QtWidgets import QWidget
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtCore import QRect, Qt
import numpy as np


class VideoWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame = None

    def set_frame(self, frame: np.ndarray):
        # frame is BGR (opencv)
        try:
            import cv2
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            bytes_per_line = ch * w
            self._qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        except Exception:
            # fallback assume frame is RGB
            h, w, ch = frame.shape
            bytes_per_line = ch * w
            self._qimg = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if hasattr(self, "_qimg") and self._qimg is not None:
            rect = QRect(0, 0, self.width(), self.height())
            painter.drawImage(rect, self._qimg.scaled(self.width(), self.height(), Qt.KeepAspectRatio))
        else:
            painter.fillRect(self.rect(), Qt.black)
