from PyQt5.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QTextEdit, QApplication
from PyQt5.QtCore import QTimer
from .video_widget import VideoWidget
from .search_panel import SearchPanel
from .alert_panel import AlertPanel
from core.stream_capture import StreamCapture
from core.frame_extractor import FrameExtractor
from detectors.green_line_detector import detect_green_and_vertical_lines
from detectors.yolo_detector import YoloDetector
from detectors.false_positive_filter import FalsePositiveFilter
from utils.logger import get_logger
import threading
import numpy as np

logger = get_logger("main_window")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NVR AI Platform")
        self._init_ui()
        self.capture = StreamCapture()
        self.extractor = FrameExtractor(self.capture)
        self.detector = YoloDetector()
        self.filter = FalsePositiveFilter()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_frame)
        self._running = False

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        h = QHBoxLayout(central)

        left = QVBoxLayout()
        self.video = VideoWidget(self)
        left.addWidget(self.video)
        ctrl = QWidget()
        cbox = QHBoxLayout(ctrl)
        self.btn_start = QPushButton("Start", self)
        self.btn_stop = QPushButton("Stop", self)
        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)
        cbox.addWidget(self.btn_start)
        cbox.addWidget(self.btn_stop)
        left.addWidget(ctrl)

        right = QVBoxLayout()
        self.search = SearchPanel(self)
        self.alerts = AlertPanel(self)
        self.log = QTextEdit(self)
        self.log.setReadOnly(True)
        right.addWidget(self.search)
        right.addWidget(self.alerts)
        right.addWidget(self.log)

        h.addLayout(left, 3)
        h.addLayout(right, 1)

        self.search.searchRequested.connect(self._on_search)

    def start(self):
        if self._running:
            return
        self.capture.start()
        self.extractor.callback = self._on_frame
        self.extractor.start()
        self.timer.start(50)
        self._running = True
        self._log("Started")

    def stop(self):
        if not self._running:
            return
        self.timer.stop()
        self.extractor.stop()
        self.capture.stop()
        self._running = False
        self._log("Stopped")

    def _on_frame(self, ts, frame):
        # called in extractor thread; push to UI via stored latest
        self._latest = (ts, frame)

    def _poll_frame(self):
        if hasattr(self, "_latest"):
            ts, frame = self._latest
            try:
                # run detectors in background to avoid blocking UI
                threading.Thread(target=self._process_frame, args=(ts, frame), daemon=True).start()
            except Exception:
                pass
            self.video.set_frame(frame)

    def _process_frame(self, ts, frame):
        try:
            g = detect_green_and_vertical_lines(frame)
            if g.get("abnormal"):
                self.alerts.add_alert(f"Green/line alert at {ts}: ratio={g.get('green_ratio'):.3f}")
                self._log(f"Green/line alert: {g.get('green_ratio'):.3f}")

            dets = self.detector.detect(frame)
            ok = self.filter.filter(dets)
            if ok:
                self._log(f"Detections: {len(ok)}")
        except Exception as e:
            logger.exception("Processing error: %s", e)

    def _on_search(self, text: str):
        self._log(f"Search requested: {text}")
        # minimal: perform search using core.searcher.Searcher
        try:
            from core.searcher import Searcher
            s = Searcher()
            results = s.search_text(text)
            self.search.show_results(results)
        except Exception as e:
            self._log(f"Search failed: {e}")

    def _log(self, text: str):
        self.log.append(text)
