from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QTextEdit, QApplication,
    QLabel, QLineEdit, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QSplitter, QListWidget
)
from PyQt5.QtCore import QTimer, Qt, QElapsedTimer
from .video_widget import VideoWidget
from core.stream_capture import StreamCapture
from core.frame_extractor import FrameExtractor
from detectors.green_line_detector import detect_green_and_vertical_lines
from detectors.yolo_detector import YoloDetector
from detectors.false_positive_filter import FalsePositiveFilter
from utils.logger import get_logger
from config import settings
from utils import video_player
import threading
import numpy as np
import csv
import os

logger = get_logger("main_window")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NVR AI Platform - 实时监控 + 文搜回溯")
        self._init_ui()
        self.capture = StreamCapture(settings.RTSP_URL)
        self.extractor = FrameExtractor(self.capture, sample_fps=settings.SAMPLE_FPS)
        self.detector = YoloDetector()
        self.filter = FalsePositiveFilter()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_frame)
        self._running = False
        self._latest = None
        self._green_consecutive = 0
        self._elapsed = QElapsedTimer()
        self._elapsed.start()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_v = QVBoxLayout(central)

        # Video area (majority)
        self.video = VideoWidget(self)
        self.video.setMinimumHeight(480)

        # RTSP control bar
        rtsp_bar = QWidget(self)
        rtsp_layout = QHBoxLayout(rtsp_bar)
        self.rtsp_input = QLineEdit(self)
        self.rtsp_input.setText(settings.RTSP_URL)
        self.btn_start = QPushButton("▶ 开始预览", self)
        self.btn_stop = QPushButton("⏹ 停止预览", self)
        self.status_lbl = QLabel("状态: ● 未连接", self)
        self.fps_lbl = QLabel("帧率: 0fps", self)
        rtsp_layout.addWidget(QLabel("RTSP地址:", self))
        rtsp_layout.addWidget(self.rtsp_input)
        rtsp_layout.addWidget(self.btn_start)
        rtsp_layout.addWidget(self.btn_stop)
        rtsp_layout.addWidget(self.status_lbl)
        rtsp_layout.addWidget(self.fps_lbl)

        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)

        # Search area
        search_bar = QWidget(self)
        s_layout = QHBoxLayout(search_bar)
        s_layout.addWidget(QLabel("📝 文字搜索（文搜图）"))
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("搜索词...")
        self.btn_search = QPushButton("🔍 搜索", self)
        self.topk_combo = QComboBox(self)
        self.topk_combo.addItems(["5", "10", "20"])
        self.btn_export = QPushButton("📊 导出CSV", self)
        s_layout.addWidget(QLabel("搜索词:"))
        s_layout.addWidget(self.search_input)
        s_layout.addWidget(self.btn_search)
        s_layout.addWidget(QLabel("TOP_K:"))
        s_layout.addWidget(self.topk_combo)
        s_layout.addWidget(self.btn_export)

        self.btn_search.clicked.connect(self._on_search)
        self.btn_export.clicked.connect(self._on_export)

        # Results table
        self.results_table = QTableWidget(0, 5, self)
        self.results_table.setHorizontalHeaderLabels(["#", "时间戳", "相似度", "通道号", "操作"])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.results_table.cellDoubleClicked.connect(self._on_result_double)

        # Bottom status: logs + alerts + runtime
        bottom_split = QSplitter(Qt.Horizontal)
        self.log = QTextEdit(self)
        self.log.setReadOnly(True)
        self.alerts = QListWidget(self)
        self.runtime_lbl = QLabel("运行时间: 00:00:00", self)
        bottom_split.addWidget(self.log)
        bottom_split.addWidget(self.alerts)

        main_v.addWidget(self.video, stretch=6)
        main_v.addWidget(rtsp_bar)
        main_v.addWidget(search_bar)
        main_v.addWidget(self.results_table, stretch=3)
        main_v.addWidget(bottom_split, stretch=2)
        main_v.addWidget(self.runtime_lbl)

        # timer to update runtime
        self.runtime_timer = QTimer(self)
        self.runtime_timer.timeout.connect(self._update_runtime)
        self.runtime_timer.start(1000)

    def start(self):
        if self._running:
            return
        url = self.rtsp_input.text().strip()
        if url:
            self.capture = StreamCapture(url)
            self.extractor = FrameExtractor(self.capture, sample_fps=settings.SAMPLE_FPS)
        self.capture.start()
        self.extractor.callback = self._on_frame
        self.extractor.start()
        self.timer.start(40)
        self._running = True
        self.status_lbl.setText("状态: ● 已连接")
        self._log("已开始预览")

    def stop(self):
        if not self._running:
            return
        self.timer.stop()
        self.extractor.stop()
        self.capture.stop()
        self._running = False
        self.status_lbl.setText("状态: ● 未连接")
        self._log("已停止预览")

    def _on_frame(self, ts, frame):
        # called in background thread
        self._latest = (ts, frame)

    def _poll_frame(self):
        if self._latest is None:
            return
        ts, frame = self._latest
        # display immediately
        self.video.set_frame(frame)
        # process detectors in background
        threading.Thread(target=self._process_frame, args=(ts, frame), daemon=True).start()

    def _process_frame(self, ts, frame):
        try:
            g = detect_green_and_vertical_lines(frame)
            # use consecutive logic
            if g.get("green_ratio", 0) > settings.GREEN_LINE_THRESHOLD:
                self._green_consecutive += 1
            else:
                self._green_consecutive = 0
            if self._green_consecutive >= settings.GREEN_LINE_CONSECUTIVE:
                self.alerts.addItem(f"Green/line alert at {ts}: ratio={g.get('green_ratio'):.3f}")
                self._log(f"Green/line alert: {g.get('green_ratio'):.3f}")
                self._green_consecutive = 0

            dets = self.detector.detect(frame)
            ok = self.filter.filter(dets)
            if ok:
                self._log(f"Detections: {len(ok)}")
        except Exception as e:
            logger.exception("Processing error: %s", e)

    def _on_search(self):
        text = self.search_input.text().strip()
        if not text:
            return
        self._log(f"Search requested: {text}")
        threading.Thread(target=self._do_search, args=(text,), daemon=True).start()

    def _do_search(self, text):
        try:
            from core.searcher import Searcher
            s = Searcher()
            topk = int(self.topk_combo.currentText())
            results = s.search_text(text, top_k=topk)
            # populate table in main thread
            def populate():
                self.results_table.setRowCount(0)
                for i, r in enumerate(results, 1):
                    meta = r.get('meta', {})
                    row = self.results_table.rowCount()
                    self.results_table.insertRow(row)
                    self.results_table.setItem(row, 0, QTableWidgetItem(str(i)))
                    self.results_table.setItem(row, 1, QTableWidgetItem(str(meta.get('ts', ''))))
                    self.results_table.setItem(row, 2, QTableWidgetItem(f"{r.get('score',0):.3f}"))
                    self.results_table.setItem(row, 3, QTableWidgetItem(str(meta.get('channel', ''))))
                    self.results_table.setItem(row, 4, QTableWidgetItem("▶ 跳转播放"))
                    # store meta on last cell
                    self.results_table.item(row, 4).setData(Qt.UserRole, r)
            self.results_table.window().invokeMethod = None
            self.results_table.window().thread = None
            self.results_table.window().setUpdatesEnabled(True)
            self.results_table.window().update()
            self.results_table.window().repaint()
            self.results_table.window().activateWindow()
            # schedule populate on main thread
            QApplication.instance().postEvent(self, lambda: None)
            # direct call (safe because results not GUI heavy)
            populate()
        except Exception as e:
            self._log(f"Search failed: {e}")

    def _on_result_double(self, row, col):
        item = self.results_table.item(row, 4)
        if not item:
            return
        r = item.data(Qt.UserRole)
        if not r:
            return
        meta = r.get('meta', {})
        video = meta.get('video')
        ts = meta.get('ts')
        if video:
            # try to play via ffplay
            try:
                sec = float(ts)
            except Exception:
                sec = 0.0
            video_player.play_video_at(video, sec)

    def _on_export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", "results.csv", "CSV Files (*.csv)")
        if not path:
            return
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["#", "时间戳", "相似度", "通道号"])
            for r in range(self.results_table.rowCount()):
                idx = self.results_table.item(r, 0).text() if self.results_table.item(r,0) else ''
                ts = self.results_table.item(r, 1).text() if self.results_table.item(r,1) else ''
                sim = self.results_table.item(r, 2).text() if self.results_table.item(r,2) else ''
                ch = self.results_table.item(r, 3).text() if self.results_table.item(r,3) else ''
                writer.writerow([idx, ts, sim, ch])
        self._log(f"搜索结果已导出: {path}")

    def _update_runtime(self):
        secs = int(self._elapsed.elapsed() / 1000)
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        self.runtime_lbl.setText(f"运行时间: {h:02d}:{m:02d}:{s:02d}")

    def _log(self, text: str):
        self.log.append(text)
