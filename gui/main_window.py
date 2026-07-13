"""
MainWindow — NVR AI 平台主窗口（多线程重构版）

架构：
- 主线程：只负责接收信号 → 更新 UI，不做任何耗时操作
- PreviewThread：RTSP 拉流 + 解码，通过 frame_ready 信号发送帧
- SearchThread：CLIP 文搜图，每次搜索新建线程
- IndexThread：离线视频索引构建，每次构建新建线程
- FTPMonitor：watchdog 监控 FTP 上传目录（QThread）
- VerificationWorker：FTP 报警文件核验（QThread，共享 YoloDetector）
- 检测处理：独立 background daemon thread（通过队列接收帧）

线程间通信：全部使用 PyQt5 Signal/Slot，禁止共享变量轮询。
帧传递：使用 .copy() 深拷贝，避免引用冲突。
"""
import os
import time
import csv
import queue
import threading
from typing import Optional

import numpy as np
import cv2

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QTextEdit, QLabel, QLineEdit, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QSplitter, QDoubleSpinBox, QProgressBar, QCheckBox,
    QFrame,
)
from PyQt5.QtCore import QTimer, Qt, QElapsedTimer, pyqtSlot

from gui.video_widget import VideoWidget
from gui.alert_panel import AlertPanel
from core.preview_thread import PreviewThread
from core.search_thread import SearchThread
from core.index_thread import IndexThread
from core.realtime_indexer import RealtimeIndexer
from core.ftp_monitor import FTPMonitor
from core.verification_worker import VerificationWorker
from detectors.green_line_detector import detect_green_and_vertical_lines
from detectors.yolo_detector import YoloDetector
from detectors.false_positive_filter import FalsePositiveFilter
from utils.logger import get_logger
from utils import video_player
from config import settings

try:
    import psutil
except Exception:
    psutil = None

logger = get_logger("main_window")


class MainWindow(QMainWindow):
    """NVR AI 平台主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("NVR AI Platform")

        # ── 线程引用 ──────────────────────────────────
        self._preview_thread: Optional[PreviewThread] = None
        self._search_thread: Optional[SearchThread] = None
        self._index_thread: Optional[IndexThread] = None
        self._ftp_monitor: Optional[FTPMonitor] = None
        self._verification_worker: Optional[VerificationWorker] = None

        # ── 帧缓冲（主线程 QTimer 15fps 显示用）───────
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_ts: float = 0.0
        self._latest_detections: list = []  # daemon 线程写入，主线程读（GIL 保证原子性）

        # ── YOLO 类别名映射 ────────────────────────────
        self._coco_names = {
            0: "person", 1: "bicycle", 2: "car", 3: "motorcycle",
            5: "bus", 7: "truck", 9: "traffic light",
            14: "bird", 15: "cat", 16: "dog",
            25: "umbrella", 39: "bottle", 41: "cup",
            56: "chair", 63: "laptop", 64: "mouse", 67: "cell phone",
        }

        # ── 帧率统计 ──────────────────────────────────
        self._recv_count: int = 0
        self._recv_last_ts: float = time.time()
        self._recv_fps: float = 0.0
        self._green_consecutive: int = 0

        # ── 检测器 ────────────────────────────────────
        self.detector = YoloDetector()
        self.fp_filter = FalsePositiveFilter()

        # ── 实时索引器（主线程持有，SearchThread 在实时模式下使用）─
        self.indexer = RealtimeIndexer(device=settings.get_device())

        # ── 检测处理队列（独立后台线程）───────────────
        self._process_queue: queue.Queue = queue.Queue(maxsize=4)
        self._process_thread: Optional[threading.Thread] = None
        self._process_running: bool = False

        # ── 运行状态 ──────────────────────────────────
        self._running: bool = False
        self._ftp_monitoring: bool = False
        self._elapsed = QElapsedTimer()

        # ── 构建 UI ───────────────────────────────────
        self._init_ui()

        # ── 运行时间定时器 ────────────────────────────
        self._runtime_timer = QTimer(self)
        self._runtime_timer.timeout.connect(self._update_runtime)
        self._runtime_timer.start(1000)

        # ── 显示刷新定时器（15fps）────────────────────
        self._display_timer = QTimer(self)
        self._display_timer.timeout.connect(self._refresh_display)

    # ═══════════════════════════════════════════════════════
    # UI 初始化
    # ═══════════════════════════════════════════════════════

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_v = QVBoxLayout(central)

        # ── 视频区域 ──────────────────────────────────
        self.video = VideoWidget(self)
        self.video.setMinimumHeight(480)

        # ── RTSP 控制栏 ───────────────────────────────
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
        rtsp_layout.addWidget(QLabel("解码模式:", self))
        self.decode_combo = QComboBox(self)
        self.decode_combo.addItems(["CPU 软解码", "FFmpeg 硬解码"])
        rtsp_layout.addWidget(self.decode_combo)
        rtsp_layout.addWidget(self.status_lbl)
        rtsp_layout.addWidget(self.fps_lbl)

        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)

        # ── YOLO 画框开关 ───────────────────────────
        self.chk_show_boxes = QCheckBox("YOLO框", self)
        self.chk_show_boxes.setChecked(settings.SHOW_YOLO_BOXES)
        self.chk_show_boxes.setToolTip("勾选后在画面上绘制 YOLO 检测框")
        rtsp_layout.addWidget(self.chk_show_boxes)

        # ── 搜索栏 ────────────────────────────────────
        search_bar = QWidget(self)
        s_layout = QHBoxLayout(search_bar)
        s_layout.addWidget(QLabel("📝 文字搜索（文搜图）"))
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("搜索词...")
        self.source_combo = QComboBox(self)
        self.source_combo.addItems(["历史索引", "实时抽帧"])
        self.btn_index_video = QPushButton("📁 构建视频索引", self)
        self.btn_search = QPushButton("🔍 搜索", self)
        self.topk_combo = QComboBox(self)
        self.topk_combo.addItems(["5", "10", "20"])
        self.duration_input = QDoubleSpinBox(self)
        self.duration_input.setRange(1, 120)
        self.duration_input.setValue(10)
        self.duration_input.setSuffix("s")
        self.btn_export = QPushButton("📊 导出结果", self)
        s_layout.addWidget(QLabel("搜索词:"))
        s_layout.addWidget(self.search_input)
        s_layout.addWidget(QLabel("源:"))
        s_layout.addWidget(self.source_combo)
        s_layout.addWidget(self.btn_index_video)
        s_layout.addWidget(self.btn_search)
        s_layout.addWidget(QLabel("TOP_K:"))
        s_layout.addWidget(self.topk_combo)
        s_layout.addWidget(QLabel("播放时长:"))
        s_layout.addWidget(self.duration_input)
        s_layout.addWidget(self.btn_export)

        self.btn_search.clicked.connect(self._on_search)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_index_video.clicked.connect(self._on_index_video)

        # ── 索引进度条 ────────────────────────────────
        progress_bar_layout = QHBoxLayout()
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.btn_cancel_index = QPushButton("取消构建", self)
        self.btn_cancel_index.setVisible(False)
        self.btn_cancel_index.clicked.connect(self._on_cancel_index)
        progress_bar_layout.addWidget(self.progress_bar)
        progress_bar_layout.addWidget(self.btn_cancel_index)

        # ── FTP 监控控制栏 ───────────────────────────
        ftp_bar = QWidget(self)
        ftp_bar_layout = QHBoxLayout(ftp_bar)
        ftp_bar_layout.setContentsMargins(0, 0, 0, 0)
        ftp_bar_layout.addWidget(QLabel("📁 FTP监控目录:", self))
        self.ftp_dir_input = QLineEdit(self)
        self.ftp_dir_input.setText(settings.FTP_UPLOAD_DIR)
        self.ftp_dir_input.setToolTip("Reolink NVR FTP 上传目录路径")
        ftp_bar_layout.addWidget(self.ftp_dir_input)
        self.btn_ftp_start = QPushButton("▶ 开始FTP监控", self)
        self.btn_ftp_stop = QPushButton("⏹ 停止FTP监控", self)
        self.btn_ftp_stop.setEnabled(False)
        self.ftp_status_lbl = QLabel("⚪ 未启动", self)
        self.ftp_queue_lbl = QLabel("", self)
        ftp_bar_layout.addWidget(self.btn_ftp_start)
        ftp_bar_layout.addWidget(self.btn_ftp_stop)
        ftp_bar_layout.addWidget(self.ftp_status_lbl)
        ftp_bar_layout.addWidget(self.ftp_queue_lbl)
        ftp_bar_layout.addStretch()

        self.btn_ftp_start.clicked.connect(self._start_ftp_monitoring)
        self.btn_ftp_stop.clicked.connect(self._stop_ftp_monitoring)

        # ── 结果表格 ──────────────────────────────────
        self.results_table = QTableWidget(0, 5, self)
        self.results_table.setHorizontalHeaderLabels(
            ["#", "时间戳", "相似度", "源", "操作"]
        )
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.results_table.cellDoubleClicked.connect(self._on_result_double)

        # ── 底部：日志 + 报警面板 + 运行时间 ──────────
        bottom_split = QSplitter(Qt.Horizontal)
        self.log_widget = QTextEdit(self)
        self.log_widget.setReadOnly(True)
        self.alert_panel = AlertPanel(self)
        self.alert_panel.set_total_channels(settings.NVR_TOTAL_CHANNELS)
        self.runtime_lbl = QLabel("运行时间: 00:00:00", self)
        bottom_split.addWidget(self.log_widget)
        bottom_split.addWidget(self.alert_panel)

        # ── 组装 ──────────────────────────────────────
        main_v.addWidget(self.video, stretch=6)
        main_v.addWidget(rtsp_bar)
        main_v.addWidget(search_bar)
        main_v.addWidget(ftp_bar)
        main_layout_widget = QWidget(self)
        main_layout_widget.setLayout(progress_bar_layout)
        main_v.addWidget(main_layout_widget)
        main_v.addWidget(self.results_table, stretch=3)
        main_v.addWidget(bottom_split, stretch=3)
        main_v.addWidget(self.runtime_lbl)

    # ═══════════════════════════════════════════════════════
    # 启动 / 停止
    # ═══════════════════════════════════════════════════════

    def start(self):
        """启动预览"""
        if self._running:
            return

        url = self.rtsp_input.text().strip() or settings.RTSP_URL

        # 解码模式
        if self.decode_combo.currentText().startswith("FFmpeg"):
            settings.USE_FFMPEG_DECODE = True
            decode_mode = "gpu"
        else:
            settings.USE_FFMPEG_DECODE = False
            decode_mode = "cpu"

        # ── 创建 PreviewThread ────────────────────────
        self._preview_thread = PreviewThread(url, parent=self)
        self._preview_thread.frame_ready.connect(self._on_frame_received)
        self._preview_thread.status_updated.connect(self._on_status_updated)
        self._preview_thread.fps_updated.connect(self._on_fps_updated)
        self._preview_thread.set_decode_mode(decode_mode)
        self._preview_thread.start()

        # ── 启动检测处理线程 ──────────────────────────
        self._start_process_worker()

        # ── 启动显示定时器（15fps）────────────────────
        self._display_timer.start(1000 // settings.UI_REFRESH_FPS)

        self._running = True
        self._elapsed.start()

        self.status_lbl.setText("状态: ● 已连接")
        self._log(f"已开始预览，解码模式={self.decode_combo.currentText()}")

    def stop(self):
        """停止预览（优雅退出）"""
        if not self._running:
            return

        self._running = False

        # 1. 停止显示定时器
        self._display_timer.stop()

        # 2. 停止检测处理线程
        self._stop_process_worker()

        # 3. 优雅停止 PreviewThread
        if self._preview_thread is not None:
            pt = self._preview_thread
            self._log("正在停止预览线程...")
            pt.stop()
            pt.quit()
            if not pt.wait(5000):
                logger.warning("PreviewThread did not finish in 5s, terminating")
                pt.terminate()
                pt.wait(3000)
            self._preview_thread = None

        # 4. 取消正在进行的搜索
        self._cancel_search()

        # 5. 取消正在进行的索引构建
        self._cancel_index_build()

        self._latest_frame = None
        self.status_lbl.setText("状态: ● 未连接")
        self.fps_lbl.setText("帧率: 0fps")
        self._log("已停止预览")

    def closeEvent(self, event):
        """窗口关闭时优雅退出所有线程"""
        self._stop_ftp_monitoring()
        self.stop()
        event.accept()

    # ═══════════════════════════════════════════════════════
    # FTP 监控管理
    # ═══════════════════════════════════════════════════════

    def _start_ftp_monitoring(self):
        """启动 FTP 目录监控 + 核验工作线程"""
        if self._ftp_monitoring:
            return

        watch_dir = self.ftp_dir_input.text().strip()
        if not watch_dir or not os.path.isdir(watch_dir):
            self._log(f"FTP 监控目录不存在: {watch_dir}")
            self.ftp_status_lbl.setText("❌ 目录不存在")
            return

        # ── 1. 启动 FTP 文件监控 ──────────────────────
        self._ftp_monitor = FTPMonitor(watch_dir=watch_dir, parent=self)
        self._ftp_monitor.file_detected.connect(self._on_ftp_file_detected)
        self._ftp_monitor.monitor_error.connect(self._on_ftp_monitor_error)
        self._ftp_monitor.monitor_status.connect(self._on_ftp_monitor_status)
        self._ftp_monitor.start()

        # ── 2. 启动核验工作线程 ────────────────────────
        profile_path = str(settings.NVR_PROFILE_PATH)
        self._verification_worker = VerificationWorker(
            detector=self.detector,
            profile_path=profile_path,
            parent=self,
        )
        self._verification_worker.verification_complete.connect(
            self._on_verification_complete
        )
        self._verification_worker.verification_error.connect(
            self._on_verification_error
        )
        self._verification_worker.worker_status.connect(
            lambda s: self.ftp_queue_lbl.setText(s)
        )
        self._verification_worker.start()

        self._ftp_monitoring = True
        self.btn_ftp_start.setEnabled(False)
        self.btn_ftp_stop.setEnabled(True)
        self.ftp_status_lbl.setText("🟢 监控中")
        self._log(f"FTP 监控已启动: {watch_dir}")

    def _stop_ftp_monitoring(self):
        """停止 FTP 监控 + 核验线程"""
        if not self._ftp_monitoring:
            return

        self._ftp_monitoring = False

        # 停止 FTP 监控
        if self._ftp_monitor is not None:
            fm = self._ftp_monitor
            fm.stop()
            fm.quit()
            if not fm.wait(5000):
                logger.warning("FTPMonitor did not finish in 5s")
                fm.terminate()
                fm.wait(3000)
            self._ftp_monitor = None

        # 停止核验线程（等待当前任务完成）
        if self._verification_worker is not None:
            vw = self._verification_worker
            vw.stop()
            vw.quit()
            if not vw.wait(5000):
                logger.warning("VerificationWorker did not finish in 5s")
                vw.terminate()
                vw.wait(3000)
            self._verification_worker = None

        self.btn_ftp_start.setEnabled(True)
        self.btn_ftp_stop.setEnabled(False)
        self.ftp_status_lbl.setText("⚪ 已停止")
        self.ftp_queue_lbl.setText("")
        self._log("FTP 监控已停止")

    # ── FTP 监控信号槽 ──────────────────────────────

    @pyqtSlot(dict)
    def _on_ftp_file_detected(self, record: dict):
        """FTP 新文件到达 → 添加到报警面板 → 加入核验队列"""
        self._log(
            f"FTP: {record.get('original', '?')} "
            f"ch={record.get('channel', '?')} "
            f"type={record.get('alarm_type', '?')}"
        )

        # 添加到报警面板（处理中状态）
        self.alert_panel.add_pending(record)

        # 加入核验队列
        if self._verification_worker:
            self._verification_worker.enqueue(record)
            pending = self._verification_worker.pending_count
            self.ftp_queue_lbl.setText(f"待处理: {pending}")

    @pyqtSlot(dict)
    def _on_verification_complete(self, result: dict):
        """核验完成 → 更新报警面板行 + 刷新报告"""
        filename = result.get("filename", "")
        self.alert_panel.update_alert(filename, result)

        # 刷新测试报告
        self.alert_panel.refresh_report()

        # 更新队列计数
        if self._verification_worker:
            pending = self._verification_worker.pending_count
            self.ftp_queue_lbl.setText(f"待处理: {pending}" if pending > 0 else "✅ 全部完成")

        # 记录日志
        is_false = result.get("is_false_alarm", False)
        status_text = "⚠️误报" if is_false else "✅正常"
        self._log(
            f"核验: {filename} → {status_text} "
            f"(置信度={result.get('yolo_max_confidence', 0):.2f})"
        )

    @pyqtSlot(str, str)
    def _on_verification_error(self, filepath: str, error: str):
        """核验出错"""
        self._log(f"核验错误: {filepath} — {error}")

    @pyqtSlot(str)
    def _on_ftp_monitor_error(self, error: str):
        """FTP 监控错误"""
        self._log(f"FTP 监控错误: {error}")
        self.ftp_status_lbl.setText(f"❌ 错误")

    @pyqtSlot(str)
    def _on_ftp_monitor_status(self, status: str):
        """FTP 监控状态更新"""
        self._log(f"FTP: {status}")

    # ═══════════════════════════════════════════════════════
    # PreviewThread 信号槽
    # ═══════════════════════════════════════════════════════

    @pyqtSlot(np.ndarray)
    def _on_frame_received(self, frame: np.ndarray):
        """
        接收 PreviewThread 发送的帧（在调用线程即主线程中执行）。
        仅存储最新帧引用，不做耗时操作。
        """
        if frame is not None and isinstance(frame, np.ndarray) and frame.size > 0:
            self._latest_frame = frame
            self._latest_ts = time.time()

            # 帧率统计
            self._recv_count += 1
            now = time.time()
            if now - self._recv_last_ts >= 1.0:
                self._recv_fps = self._recv_count / (now - self._recv_last_ts)
                self._recv_count = 0
                self._recv_last_ts = now

    @pyqtSlot(str)
    def _on_status_updated(self, status: str):
        """接收 PreviewThread 的状态更新"""
        self.status_lbl.setText(f"状态: {status}")

    @pyqtSlot(float)
    def _on_fps_updated(self, fps: float):
        """接收 PreviewThread 的实际拉流帧率"""
        self.fps_lbl.setText(f"帧率: {fps:.0f}fps")

    # ═══════════════════════════════════════════════════════
    # 显示刷新（QTimer 15fps）
    # ═══════════════════════════════════════════════════════

    def _refresh_display(self):
        """
        由 QTimer 每 1/15 秒触发。
        将最新帧渲染到 VideoWidget，并将帧入队给检测处理线程。
        """
        if self._latest_frame is None:
            return

        frame = self._latest_frame

        # ── Overlay 信息 ──────────────────────────────
        backend = ""
        if self._preview_thread is not None:
            backend = self._preview_thread.backend or ""

        # 视频源 FPS
        cap_fps = 0.0
        try:
            if (
                self._preview_thread is not None
                and self._preview_thread.cap is not None
                and self._preview_thread.cap.isOpened()
            ):
                cap_fps = float(
                    self._preview_thread.cap.get(cv2.CAP_PROP_FPS) or 0.0
                )
        except Exception:
            pass

        # CPU 使用率
        cpu_pct = None
        try:
            if psutil is not None:
                p = psutil.Process(os.getpid())
                cpu_pct = p.cpu_percent(interval=0.0)
        except Exception:
            pass

        # 状态
        status = "● 未连接"
        if self._running:
            status = "● 已连接"

        # ── 刷新视频显示 ──────────────────────────────
        # 如果开启了画框开关且有检测结果，在帧上画框
        detections = self._latest_detections if hasattr(self, '_latest_detections') else []
        display_frame = frame
        if detections and self.chk_show_boxes.isChecked():
            display_frame = frame.copy()
            self._draw_boxes(display_frame, detections)

        self.video.set_overlay_info(
            backend=backend,
            fps=cap_fps,
            status=status,
            cpu=cpu_pct,
            recv_fps=self._recv_fps,
        )
        self.video.set_frame(display_frame)

        # ── 入队检测处理（不阻塞显示）─────────────────
        try:
            self._process_queue.put_nowait((self._latest_ts, frame))
        except queue.Full:
            try:
                self._process_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._process_queue.put_nowait((self._latest_ts, frame))
            except queue.Full:
                pass

    # ═══════════════════════════════════════════════════════
    # 检测处理线程（后台 daemon thread）
    # ═══════════════════════════════════════════════════════

    def _start_process_worker(self):
        if self._process_thread and self._process_thread.is_alive():
            return
        self._process_running = True
        self._process_thread = threading.Thread(
            target=self._process_loop, daemon=True
        )
        self._process_thread.start()

    def _stop_process_worker(self):
        self._process_running = False
        try:
            while not self._process_queue.empty():
                self._process_queue.get_nowait()
        except Exception:
            pass
        if self._process_thread:
            self._process_thread.join(timeout=5)
        self._process_thread = None

    def _process_loop(self):
        """检测处理循环（独立 daemon thread）"""
        while self._process_running:
            try:
                item = self._process_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                continue
            ts, frame = item
            self._process_frame(ts, frame)

    def _process_frame(self, ts: float, frame: np.ndarray):
        """
        执行帧检测处理（在后台线程中调用）。
        - 绿线 / 花屏检测
        - YOLO 目标检测
        - 实时索引加入
        """
        try:
            # 绿线检测
            g = detect_green_and_vertical_lines(frame)
            if g.get("green_ratio", 0) > settings.GREEN_LINE_THRESHOLD:
                self._green_consecutive += 1
            else:
                self._green_consecutive = 0

            if self._green_consecutive >= settings.GREEN_LINE_CONSECUTIVE:
                # 发射告警到报警面板（文本模式，兼容旧版）
                alert_text = (
                    f"Green/line alert at {ts:.2f}: "
                    f"ratio={g.get('green_ratio', 0):.3f}"
                )
                # 使用信号方式日志记录
                self._log(alert_text)
                self.alert_panel.add_alert(alert_text)
                self._green_consecutive = 0

            # YOLO 检测
            dets = self.detector.detect(frame)
            ok_dets = self.fp_filter.filter(dets)
            # ⚠️ 先存检测结果，再写日志（如果 _log 抛异常不会丢掉结果）
            self._latest_detections = ok_dets
            if ok_dets:
                self._log(f"Detections: {len(ok_dets)}")

            # 实时索引
            try:
                backend = ""
                if self._preview_thread is not None:
                    backend = self._preview_thread.backend or ""
                url = ""
                if self._preview_thread is not None:
                    url = self._preview_thread.rtsp_url or ""
                self.indexer.add_frame(frame, ts, video_url=url, backend=backend)
            except Exception:
                logger.exception("Realtime indexing error")

        except Exception as e:
            logger.exception("Processing error: %s", e)

    # ═══════════════════════════════════════════════════════
    # 画框（在主线程 _refresh_display 中调用）
    # ═══════════════════════════════════════════════════════

    # 每个 COCO 类别分配一种颜色（BGR），方便区分不同物体类型
    _CLASS_COLORS = {
        0: (0, 255, 0),      # person — 绿色
        1: (255, 255, 0),    # bicycle — 天蓝
        2: (0, 165, 255),    # car — 橙色
        3: (255, 0, 255),    # motorcycle — 品红
        5: (0, 255, 255),    # bus — 黄色
        7: (128, 0, 128),    # truck — 紫色
        9: (0, 140, 255),    # traffic light — 深橙
        14: (255, 255, 255), # bird — 白色
        15: (255, 0, 0),     # cat — 蓝色
        16: (0, 0, 255),     # dog — 红色
        25: (128, 128, 128), # umbrella — 灰色
        39: (0, 255, 128),   # bottle — 青绿
        41: (200, 100, 0),   # cup — 湖蓝
        56: (50, 50, 200),   # chair — 浅红
        63: (200, 200, 0),   # laptop — 蓝绿
        64: (180, 0, 180),   # mouse — 紫红
        67: (100, 255, 100), # cell phone — 浅绿
    }

    def _draw_boxes(self, frame: np.ndarray, detections: list):
        """
        在帧上绘制 YOLO 检测框（在传入的 frame 上原地修改）。
        每个检测 [x1, y1, x2, y2, confidence, class_id]
        不同类别使用不同颜色，方便区别人/车/动物等。
        """
        import cv2
        for d in detections:
            x1, y1, x2, y2 = int(d[0]), int(d[1]), int(d[2]), int(d[3])
            conf = float(d[4])
            cls_id = int(d[5])
            color = self._CLASS_COLORS.get(cls_id, (0, 255, 0))
            label = self._coco_names.get(cls_id, f"cls_{cls_id}")
            text = f"{label} {conf:.2f}"

            # 框
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            # 标签背景（与框同色）
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1 - th - 4), (x1 + tw, y1), color, -1)
            # 标签文字（白色，更易读）
            cv2.putText(
                frame, text, (x1, y1 - 3),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1,
            )

    # ═══════════════════════════════════════════════════════
    # 搜索（SearchThread）
    # ═══════════════════════════════════════════════════════

    def _on_search(self):
        text = self.search_input.text().strip()
        if not text:
            return

        # 取消上一次搜索
        self._cancel_search()

        topk = int(self.topk_combo.currentText())
        source = self.source_combo.currentText()  # "历史索引" | "实时抽帧"
        source_key = "realtime" if source == "实时抽帧" else "history"

        self._log(f"搜索: '{text}' (源={source}, top_k={topk})")

        # 创建 SearchThread
        self._search_thread = SearchThread(parent=self)
        self._search_thread.search_started.connect(self._on_search_started)
        self._search_thread.search_finished.connect(self._on_search_finished)
        self._search_thread.search_error.connect(self._on_search_error)

        self._search_thread.setup(
            text=text,
            top_k=topk,
            source=source_key,
            frame=self._latest_frame,
            timestamp=self._latest_ts,
            realtime_indexer=self.indexer if source_key == "realtime" else None,
        )

        self.btn_search.setEnabled(False)
        self.btn_search.setText("搜索中...")
        self._search_thread.start()

    def _cancel_search(self):
        if self._search_thread is not None:
            st = self._search_thread
            if st.isRunning():
                st.stop()
                st.quit()
                st.wait(3000)
            self._search_thread = None
        self.btn_search.setEnabled(True)
        self.btn_search.setText("🔍 搜索")

    @pyqtSlot()
    def _on_search_started(self):
        self._log("搜索进行中...")

    @pyqtSlot(list)
    def _on_search_finished(self, results: list):
        """填充搜索结果表格（在主线程执行）"""
        self._log(f"搜索完成，共 {len(results)} 条结果")

        self.results_table.setRowCount(0)
        for i, r in enumerate(results, 1):
            meta = r.get("meta", {})
            similarity = r.get("similarity", r.get("score", 0.0))
            ts_val = meta.get("ts", "")

            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self.results_table.setItem(row, 0, QTableWidgetItem(str(i)))
            self.results_table.setItem(row, 1, QTableWidgetItem(str(ts_val)))
            self.results_table.setItem(
                row, 2, QTableWidgetItem(f"{similarity:.3f}")
            )
            self.results_table.setItem(
                row, 3,
                QTableWidgetItem(
                    str(meta.get("source", meta.get("channel", "")))
                ),
            )
            op_item = QTableWidgetItem("▶ 跳转播放")
            op_item.setData(Qt.UserRole, r)
            self.results_table.setItem(row, 4, op_item)

        # 搜索完成，恢复按钮状态
        self._search_thread = None
        self.btn_search.setEnabled(True)
        self.btn_search.setText("🔍 搜索")

    @pyqtSlot(str)
    def _on_search_error(self, error: str):
        self._log(f"搜索失败: {error}")
        self._search_thread = None
        self.btn_search.setEnabled(True)
        self.btn_search.setText("🔍 搜索")

    # ═══════════════════════════════════════════════════════
    # 索引构建（IndexThread）
    # ═══════════════════════════════════════════════════════

    def _on_index_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            str(settings.VIDEOS_DIR),
            "Video Files (*.mp4 *.avi *.mov *.mkv)",
        )
        if not path:
            return

        # 取消上一次构建
        self._cancel_index_build()

        self._log(f"开始构建视频索引: {path}")

        # 显示进度条
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.btn_cancel_index.setVisible(True)
        self.btn_index_video.setEnabled(False)

        # 创建 IndexThread
        self._index_thread = IndexThread(parent=self)
        self._index_thread.progress_updated.connect(self._on_index_progress)
        self._index_thread.index_built.connect(self._on_index_built)
        self._index_thread.index_error.connect(self._on_index_error)

        self._index_thread.setup(
            video_path=path,
            sample_fps=settings.VIDEO_INDEX_SAMPLE_FPS,
        )
        self._index_thread.start()

    def _on_cancel_index(self):
        if self._index_thread is not None and self._index_thread.isRunning():
            self._index_thread.cancel()
            self._log("正在取消索引构建...")

    def _cancel_index_build(self):
        if self._index_thread is not None:
            it = self._index_thread
            if it.isRunning():
                it.cancel()
                it.quit()
                it.wait(5000)
            self._index_thread = None
        self.progress_bar.setVisible(False)
        self.btn_cancel_index.setVisible(False)
        self.btn_index_video.setEnabled(True)

    @pyqtSlot(int, int)
    def _on_index_progress(self, current: int, total: int):
        """更新进度条（在主线程执行）"""
        if total > 0:
            pct = int(100 * current / total)
            self.progress_bar.setValue(min(pct, 100))

    @pyqtSlot(str)
    def _on_index_built(self, index_path: str):
        """索引构建成功（在主线程执行）"""
        self._log(f"视频索引构建完成: {index_path}")
        self._finish_index_build()

    @pyqtSlot(str)
    def _on_index_error(self, error: str):
        """索引构建失败（在主线程执行）"""
        self._log(f"视频索引构建失败: {error}")
        self._finish_index_build()

    def _finish_index_build(self):
        """清理索引构建状态"""
        self._index_thread = None
        self.progress_bar.setVisible(False)
        self.btn_cancel_index.setVisible(False)
        self.btn_index_video.setEnabled(True)

    # ═══════════════════════════════════════════════════════
    # 结果双击：跳转回放
    # ═══════════════════════════════════════════════════════

    def _on_result_double(self, row: int, _col: int):
        item = self.results_table.item(row, 4)
        if not item:
            return
        r = item.data(Qt.UserRole)
        if not r:
            return

        meta = r.get("meta", {})
        video = meta.get("video", "")
        ts = meta.get("ts", 0.0)
        duration = self.duration_input.value()

        if not video:
            return

        try:
            source = str(meta.get("source", "")).lower()
            if source == "realtime" or str(video).lower().startswith("rtsp"):
                video_player.play_video_at(video, 0.0, duration=duration)
            else:
                video_player.play_video_at(video, float(ts), duration=duration)
        except Exception:
            video_player.play_video_at(video, 0.0, duration=duration)

    # ═══════════════════════════════════════════════════════
    # 导出
    # ═══════════════════════════════════════════════════════

    def _on_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "results.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["#", "时间戳", "相似度", "源"])
            for r in range(self.results_table.rowCount()):
                idx = (
                    self.results_table.item(r, 0).text()
                    if self.results_table.item(r, 0)
                    else ""
                )
                ts = (
                    self.results_table.item(r, 1).text()
                    if self.results_table.item(r, 1)
                    else ""
                )
                sim = (
                    self.results_table.item(r, 2).text()
                    if self.results_table.item(r, 2)
                    else ""
                )
                src = (
                    self.results_table.item(r, 3).text()
                    if self.results_table.item(r, 3)
                    else ""
                )
                writer.writerow([idx, ts, sim, src])
        self._log(f"搜索结果已导出: {path}")

    # ═══════════════════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════════════════

    def _update_runtime(self):
        secs = int(self._elapsed.elapsed() / 1000) if self._running else 0
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        self.runtime_lbl.setText(f"运行时间: {h:02d}:{m:02d}:{s:02d}")

    def _log(self, text: str):
        """添加日志到日志面板"""
        self.log_widget.append(text)
