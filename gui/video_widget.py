from PyQt5.QtWidgets import QWidget
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtCore import QRect, Qt
import numpy as np


class VideoWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame = None

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
