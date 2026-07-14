"""
MainWindow — NVR AI 平台主窗口（v2.1 嵌入式 GUI 重构）

架构：
- 顶部导航栏：仿 NVR HDMI GUI 风格，4 页切换
  - 第1页：实时预览（RTSP 拉流 + YOLO 画框 + 热力图叠加）
  - 第2页：测试工具（FTP 监控 + 未来：音频、邮件核验等）
  - 第3页：报警面板（报警明细 + FTP 测试报告）
  - 第4页：日志

线程管理：
- PreviewThread：RTSP 拉流 + 解码
- SearchThread / IndexThread：按需创建
- FTPMonitor：watchdog 监控 FTP 上传目录
- VerificationWorker：FTP 报警核验
- 检测处理：独立 background daemon thread
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
    QStackedWidget, QFrame, QTabWidget, QGridLayout,
)
from PyQt5.QtCore import QTimer, Qt, QElapsedTimer, pyqtSlot

from gui.video_widget import VideoWidget
from gui.alert_panel import AlertPanel
from gui.navigation_bar import NavigationBar
from gui.search_vocabulary_panel import SearchVocabularyPanel
from gui.audio_test_widget import AudioTestWidget
from gui.config_test_widget import ConfigTestWidget
from core.preview_thread import PreviewThread
from core.search_thread import SearchThread
from core.index_thread import IndexThread
from core.realtime_indexer import RealtimeIndexer
from core.ftp_monitor import FTPMonitor
from core.verification_worker import VerificationWorker
from core.heatmap_generator import HeatmapGenerator
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
    """NVR AI 平台主窗口 v2.1"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("NVR AI Platform")
        self.resize(1280, 900)

        # ── 线程引用 ──────────────────────────────────
        self._preview_thread: Optional[PreviewThread] = None
        self._search_thread: Optional[SearchThread] = None
        self._index_thread: Optional[IndexThread] = None
        self._ftp_monitor: Optional[FTPMonitor] = None
        self._verification_worker: Optional[VerificationWorker] = None

        # ── 帧缓冲 ────────────────────────────────────
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_ts: float = 0.0
        self._latest_detections: list = []

        # ── YOLO 类别名映射 ───────────────────────────
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

        # ── 热力图 ────────────────────────────────────
        self.heatmap_gen: Optional[HeatmapGenerator] = None
        self._heatmap_enabled: bool = False

        # ── 实时索引器 ───────────────────────────────
        self.indexer = RealtimeIndexer(device=settings.get_device())

        # ── 检测处理队列 ──────────────────────────────
        self._process_queue: queue.Queue = queue.Queue(maxsize=4)
        self._process_thread: Optional[threading.Thread] = None
        self._process_running: bool = False

        # ── 运行状态 ──────────────────────────────────
        self._running: bool = False
        self._ftp_monitoring: bool = False
        self._elapsed = QElapsedTimer()

        # ── 构建 UI ───────────────────────────────────
        self._init_ui()

        # ── 定时器 ────────────────────────────────────
        self._runtime_timer = QTimer(self)
        self._runtime_timer.timeout.connect(self._update_runtime)
        self._runtime_timer.start(1000)

        self._display_timer = QTimer(self)
        self._display_timer.timeout.connect(self._refresh_display)

    # ═══════════════════════════════════════════════════════
    # UI 初始化 — 嵌入式 4 页布局
    # ═══════════════════════════════════════════════════════

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_v = QVBoxLayout(central)
        main_v.setContentsMargins(0, 0, 0, 0)
        main_v.setSpacing(0)

        # ── 顶部导航栏 ────────────────────────────────
        self.nav_bar = NavigationBar(self)
        self.nav_bar.add_tab("\U0001f3e0 实时预览")
        self.nav_bar.add_tab("\U0001f9ea 测试工具")
        self.nav_bar.add_tab("\U0001f4cb 报警")
        self.nav_bar.add_tab("\U0001f4dd 日志")
        main_v.addWidget(self.nav_bar)

        # ── 主内容区 QStackedWidget ───────────────────
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self._create_preview_page())
        self.content_stack.addWidget(self._create_test_tools_page())
        self.content_stack.addWidget(self._create_alert_page())
        self.content_stack.addWidget(self._create_log_page())

        self.nav_bar.currentChanged.connect(self.content_stack.setCurrentIndex)
        main_v.addWidget(self.content_stack, stretch=1)

        # ── 底部状态栏 ────────────────────────────────
        status_bar = QWidget()
        status_bar.setFixedHeight(28)
        status_bar.setStyleSheet("background: #16213e; color: #a0aec0; font-size: 11px;")
        sb_layout = QHBoxLayout(status_bar)
        sb_layout.setContentsMargins(12, 2, 12, 2)

        self.runtime_lbl = QLabel("运行时间: 00:00:00")
        sb_layout.addWidget(self.runtime_lbl)
        sb_layout.addStretch()

        self.heatmap_stats_lbl = QLabel("")
        sb_layout.addWidget(self.heatmap_stats_lbl)
        sb_layout.addStretch()

        sb_layout.addWidget(QLabel("NVR AI Platform v2.1"))
        main_v.addWidget(status_bar)

    # ═══════════════════════════════════════════════════════
    # 第1页：实时预览
    # ═══════════════════════════════════════════════════════

    def _create_preview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # ── 上部：视频画面 ─────────────────────────────
        self.video = VideoWidget(self)
        self.video.setMinimumHeight(360)

        # ── RTSP 控制栏 ───────────────────────────────
        rtsp_bar = QWidget()
        rtsp_layout = QHBoxLayout(rtsp_bar)
        rtsp_layout.setContentsMargins(0, 0, 0, 0)

        self.rtsp_input = QLineEdit()
        self.rtsp_input.setText(settings.RTSP_URL)
        self.rtsp_input.setPlaceholderText("RTSP 地址...")

        self.btn_start = QPushButton("▶ 开始预览")
        self.btn_stop = QPushButton("⏹ 停止预览")
        self.status_lbl = QLabel("状态: ● 未连接")
        self.fps_lbl = QLabel("帧率: 0fps")

        self.decode_combo = QComboBox()
        self.decode_combo.addItems(["CPU 软解码", "FFmpeg 硬解码"])

        self.chk_show_boxes = QCheckBox("YOLO框")
        self.chk_show_boxes.setChecked(settings.SHOW_YOLO_BOXES)
        self.chk_show_boxes.setToolTip("勾选后在画面上绘制 YOLO 检测框")

        self.chk_heatmap = QCheckBox("\U0001f525 热力图")
        self.chk_heatmap.setToolTip("开启运动热力图叠加（帧间差分累积）")
        self.chk_heatmap.toggled.connect(self._on_heatmap_toggled)

        self.btn_reset_heatmap = QPushButton("\U0001f504 重置")
        self.btn_reset_heatmap.setToolTip("重置热力矩阵，重新开始累积")
        self.btn_reset_heatmap.clicked.connect(self._on_reset_heatmap)

        rtsp_layout.addWidget(QLabel("RTSP地址:"))
        rtsp_layout.addWidget(self.rtsp_input, stretch=1)
        rtsp_layout.addWidget(self.btn_start)
        rtsp_layout.addWidget(self.btn_stop)
        rtsp_layout.addWidget(QLabel("解码:"))
        rtsp_layout.addWidget(self.decode_combo)
        rtsp_layout.addWidget(self.status_lbl)
        rtsp_layout.addWidget(self.fps_lbl)
        rtsp_layout.addWidget(self.chk_show_boxes)
        rtsp_layout.addWidget(self.chk_heatmap)
        rtsp_layout.addWidget(self.btn_reset_heatmap)

        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)

        # ── 搜索栏 ────────────────────────────────────
        search_bar = QWidget()
        s_layout = QHBoxLayout(search_bar)
        s_layout.setContentsMargins(0, 0, 0, 0)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索词（文搜图）...")
        self.source_combo = QComboBox()
        self.source_combo.addItems(["历史索引", "实时抽帧"])
        self.btn_index_video = QPushButton("\U0001f4c1 构建视频索引")
        self.btn_search = QPushButton("\U0001f50d 搜索")
        self.topk_combo = QComboBox()
        self.topk_combo.addItems(["5", "10", "20"])
        self.duration_input = QDoubleSpinBox()
        self.duration_input.setRange(1, 120)
        self.duration_input.setValue(10)
        self.duration_input.setSuffix("s")
        self.btn_export = QPushButton("\U0001f4ca 导出结果")
        self.btn_vocab = QPushButton("\U0001f4d6 词库")
        self.btn_vocab.setToolTip("打开文搜图词库看板，点击词汇快速搜索")
        self.btn_vocab.clicked.connect(self._on_open_vocabulary)

        s_layout.addWidget(QLabel("搜索词:"))
        s_layout.addWidget(self.search_input, stretch=1)
        s_layout.addWidget(QLabel("源:"))
        s_layout.addWidget(self.source_combo)
        s_layout.addWidget(self.btn_index_video)
        s_layout.addWidget(self.btn_search)
        s_layout.addWidget(QLabel("TOP_K:"))
        s_layout.addWidget(self.topk_combo)
        s_layout.addWidget(QLabel("时长:"))
        s_layout.addWidget(self.duration_input)
        s_layout.addWidget(self.btn_export)
        s_layout.addWidget(self.btn_vocab)

        self.btn_search.clicked.connect(self._on_search)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_index_video.clicked.connect(self._on_index_video)

        # ── 进度条 ────────────────────────────────────
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.btn_cancel_index = QPushButton("取消构建")
        self.btn_cancel_index.setVisible(False)
        self.btn_cancel_index.clicked.connect(self._on_cancel_index)

        progress_w = QWidget()
        progress_l = QHBoxLayout(progress_w)
        progress_l.setContentsMargins(0, 0, 0, 0)
        progress_l.addWidget(self.progress_bar)
        progress_l.addWidget(self.btn_cancel_index)

        # ── 搜索结果表格 ──────────────────────────────
        self.results_table = QTableWidget(0, 5)
        self.results_table.setHorizontalHeaderLabels(
            ["#", "时间戳", "相似度", "源", "操作"]
        )
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.results_table.cellDoubleClicked.connect(self._on_result_double)

        # ── 组装预览页 ────────────────────────────────
        layout.addWidget(self.video, stretch=6)
        layout.addWidget(rtsp_bar)
        layout.addWidget(search_bar)
        layout.addWidget(progress_w)
        layout.addWidget(self.results_table, stretch=2)

        return page

    # ═══════════════════════════════════════════════════════
    # 第2页：测试工具
    # ═══════════════════════════════════════════════════════

    def _create_test_tools_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)

        # 子 Tab：FTP 监控 / 热力图分析 / 更多...
        sub_tabs = QTabWidget()
        sub_tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #ddd; }
            QTabBar::tab { padding: 8px 16px; }
            QTabBar::tab:selected { background: #e3f2fd; font-weight: bold; }
        """)

        # ── 子页 2a: FTP 监控 ─────────────────────────
        ftp_page = QWidget()
        ftp_layout = QVBoxLayout(ftp_page)

        ftp_ctrl = QWidget()
        ftp_ctrl_l = QHBoxLayout(ftp_ctrl)
        ftp_ctrl_l.setContentsMargins(0, 0, 0, 0)
        ftp_ctrl_l.addWidget(QLabel("\U0001f4c1 FTP监控目录:"))
        self.ftp_dir_input = QLineEdit(settings.FTP_UPLOAD_DIR)
        self.ftp_dir_input.setToolTip("Reolink NVR FTP 上传目录路径")
        ftp_ctrl_l.addWidget(self.ftp_dir_input, stretch=1)
        self.btn_ftp_start = QPushButton("▶ 开始FTP监控")
        self.btn_ftp_stop = QPushButton("⏹ 停止FTP监控")
        self.btn_ftp_stop.setEnabled(False)
        self.ftp_status_lbl = QLabel("⚪ 未启动")
        self.ftp_queue_lbl = QLabel("")
        ftp_ctrl_l.addWidget(self.btn_ftp_start)
        ftp_ctrl_l.addWidget(self.btn_ftp_stop)
        ftp_ctrl_l.addWidget(self.ftp_status_lbl)
        ftp_ctrl_l.addWidget(self.ftp_queue_lbl)

        self.btn_ftp_start.clicked.connect(self._start_ftp_monitoring)
        self.btn_ftp_stop.clicked.connect(self._stop_ftp_monitoring)

        ftp_layout.addWidget(ftp_ctrl)
        ftp_layout.addStretch()
        sub_tabs.addTab(ftp_page, "\U0001f4c1 FTP 监控")

        # ── 子页 2b: 热力图分析 ───────────────────────
        heat_page = QWidget()
        heat_layout = QVBoxLayout(heat_page)

        heat_info = QLabel(
            "运动热力图分析\n\n"
            "原理：累积帧间差分到热力矩阵，蓝(冷)→绿→黄→红(热)\n"
            "用途：验证 NVR 自带热力图的准确性\n\n"
            "使用方法：\n"
            "1. 回到预览页，启动 RTSP 预览\n"
            "2. 勾选 \"\U0001f525 热力图\" 开关\n"
            "3. 观察画面叠加的伪彩色热力层\n"
            "4. 切换回本页查看统计信息"
        )
        heat_info.setWordWrap(True)
        heat_layout.addWidget(heat_info)

        self.heatmap_info_lbl = QLabel("热力图状态：未启动")
        heat_layout.addWidget(self.heatmap_info_lbl)

        heat_btn_layout = QHBoxLayout()
        self.btn_heatmap_reset = QPushButton("\U0001f504 重置热力图")
        self.btn_heatmap_reset.clicked.connect(self._on_reset_heatmap)
        heat_btn_layout.addWidget(self.btn_heatmap_reset)
        heat_btn_layout.addStretch()
        heat_layout.addLayout(heat_btn_layout)
        heat_layout.addStretch()

        sub_tabs.addTab(heat_page, "\U0001f525 热力图分析")

        # ── 子页 2c: 音频检测 ───────────────────────
        self.audio_test_widget = AudioTestWidget(self)
        sub_tabs.addTab(self.audio_test_widget, "\U0001f3a4 音频检测")

        # ── 子页 2d: NVR 配置测试 ────────────────────
        self.config_test_widget = ConfigTestWidget(self)
        sub_tabs.addTab(self.config_test_widget, "NVR配置测试")

        layout.addWidget(sub_tabs)
        return page

    # ═══════════════════════════════════════════════════════
    # 第3页：报警面板
    # ═══════════════════════════════════════════════════════

    def _create_alert_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        self.alert_panel = AlertPanel(self)
        self.alert_panel.set_total_channels(settings.NVR_TOTAL_CHANNELS)
        layout.addWidget(self.alert_panel)
        return page

    # ═══════════════════════════════════════════════════════
    # 第4页：日志
    # ═══════════════════════════════════════════════════════

    def _create_log_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)

        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        layout.addWidget(self.log_widget)
        return page

    # ═══════════════════════════════════════════════════════
    # 热力图控制
    # ═══════════════════════════════════════════════════════

    def _on_heatmap_toggled(self, enabled: bool):
        """热力图开关"""
        self._heatmap_enabled = enabled

        if enabled:
            if self.heatmap_gen is None:
                # 用当前帧尺寸初始化
                if self._latest_frame is not None:
                    h, w = self._latest_frame.shape[:2]
                else:
                    w, h = 640, 480
                self.heatmap_gen = HeatmapGenerator(width=w, height=h)
                self._log("热力图已初始化")
            self.video.set_heatmap_enabled(True)
        else:
            self.video.set_heatmap_enabled(False)
            # 更新 OSD 移除热力图标记
            self.video.clear_heatmap()

    def _on_reset_heatmap(self):
        """重置热力图"""
        if self.heatmap_gen:
            self.heatmap_gen.reset()
            self._log("热力图已重置")

    # ═══════════════════════════════════════════════════════
    # 启动 / 停止预览
    # ═══════════════════════════════════════════════════════

    def start(self):
        """启动预览"""
        if self._running:
            return

        url = self.rtsp_input.text().strip() or settings.RTSP_URL

        if self.decode_combo.currentText().startswith("FFmpeg"):
            settings.USE_FFMPEG_DECODE = True
            decode_mode = "gpu"
        else:
            settings.USE_FFMPEG_DECODE = False
            decode_mode = "cpu"

        self._preview_thread = PreviewThread(url, parent=self)
        self._preview_thread.frame_ready.connect(self._on_frame_received)
        self._preview_thread.status_updated.connect(self._on_status_updated)
        self._preview_thread.fps_updated.connect(self._on_fps_updated)
        self._preview_thread.set_decode_mode(decode_mode)
        self._preview_thread.start()

        self._start_process_worker()
        self._display_timer.start(1000 // settings.UI_REFRESH_FPS)

        self._running = True
        self._elapsed.start()

        # 初始化热力图生成器
        if self.heatmap_gen is None:
            self.heatmap_gen = HeatmapGenerator(width=640, height=480)

        self.status_lbl.setText("状态: ● 已连接")
        self._log(f"已开始预览，解码模式={self.decode_combo.currentText()}")

    def stop(self):
        """停止预览"""
        if not self._running:
            return

        self._running = False
        self._display_timer.stop()
        self._stop_process_worker()

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

        self._cancel_search()
        self._cancel_index_build()

        self._latest_frame = None
        self.status_lbl.setText("状态: ● 未连接")
        self.fps_lbl.setText("帧率: 0fps")
        self._log("已停止预览")

    def closeEvent(self, event):
        self._stop_ftp_monitoring()
        self.stop()
        event.accept()

    # ═══════════════════════════════════════════════════════
    # PreviewThread 信号
    # ═══════════════════════════════════════════════════════

    @pyqtSlot(np.ndarray)
    def _on_frame_received(self, frame: np.ndarray):
        if frame is not None and isinstance(frame, np.ndarray) and frame.size > 0:
            self._latest_frame = frame
            self._latest_ts = time.time()
            self._recv_count += 1
            now = time.time()
            if now - self._recv_last_ts >= 1.0:
                self._recv_fps = self._recv_count / (now - self._recv_last_ts)
                self._recv_count = 0
                self._recv_last_ts = now

    @pyqtSlot(str)
    def _on_status_updated(self, status: str):
        self.status_lbl.setText(f"状态: {status}")

    @pyqtSlot(float)
    def _on_fps_updated(self, fps: float):
        self.fps_lbl.setText(f"帧率: {fps:.0f}fps")

    # ═══════════════════════════════════════════════════════
    # 显示刷新 + 热力图
    # ═══════════════════════════════════════════════════════

    def _refresh_display(self):
        """QTimer 每 1/15 秒触发 — 刷新视频 + 热力图 + 入队检测"""
        if self._latest_frame is None:
            return

        frame = self._latest_frame

        # Overlay 信息
        backend = ""
        if self._preview_thread is not None:
            backend = self._preview_thread.backend or ""

        cap_fps = 0.0
        try:
            if (self._preview_thread is not None
                    and self._preview_thread.cap is not None
                    and self._preview_thread.cap.isOpened()):
                cap_fps = float(self._preview_thread.cap.get(cv2.CAP_PROP_FPS) or 0.0)
        except Exception:
            pass

        cpu_pct = None
        try:
            if psutil is not None:
                p = psutil.Process(os.getpid())
                cpu_pct = p.cpu_percent(interval=0.0)
        except Exception:
            pass

        status = "● 已连接" if self._running else "● 未连接"

        # YOLO 画框
        detections = self._latest_detections if hasattr(self, '_latest_detections') else []
        display_frame = frame
        if detections and self.chk_show_boxes.isChecked():
            display_frame = frame.copy()
            self._draw_boxes(display_frame, detections)

        # ★ 热力图叠加
        if self._heatmap_enabled and self.heatmap_gen is not None:
            display_frame = self.heatmap_gen.overlay(display_frame, alpha=0.30)

            # 更新底部状态栏热力图统计
            stats = self.heatmap_gen.get_heat_stats()
            self.heatmap_stats_lbl.setText(
                f"\U0001f525 热力图 | 运行 {stats['elapsed_sec']:.0f}s "
                f"| 最高 {stats['max_heat']:.0f} "
                f"| 峰值区 {stats['peak_regions']} 个"
            )
            self.heatmap_info_lbl.setText(
                f"热力图状态：运行中\n"
                f"累积帧数: {stats['frame_count']}\n"
                f"已运行: {stats['elapsed_sec']:.0f}s\n"
                f"全局平均热力: {stats['mean_heat']:.0f}\n"
                f"最高热力值: {stats['max_heat']:.0f}\n"
                f"峰值区域数: {stats['peak_regions']} 个\n"
                f"热力矩阵: {stats['matrix_shape'][0]}×{stats['matrix_shape'][1]}"
            )
        else:
            self.heatmap_stats_lbl.setText("")
            self.heatmap_info_lbl.setText("热力图状态：未启动")

        # 更新 VideoWidget
        self.video.set_overlay_info(
            backend=backend, fps=cap_fps, status=status,
            cpu=cpu_pct, recv_fps=self._recv_fps,
        )
        self.video.set_frame(display_frame)

        # 入队检测处理
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
    # 检测处理（保持不变）
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
        try:
            g = detect_green_and_vertical_lines(frame)
            if g.get("green_ratio", 0) > settings.GREEN_LINE_THRESHOLD:
                self._green_consecutive += 1
            else:
                self._green_consecutive = 0

            if self._green_consecutive >= settings.GREEN_LINE_CONSECUTIVE:
                alert_text = (
                    f"Green/line alert at {ts:.2f}: "
                    f"ratio={g.get('green_ratio', 0):.3f}"
                )
                self._log(alert_text)
                self.alert_panel.add_alert(alert_text)
                self._green_consecutive = 0

            dets = self.detector.detect(frame)
            ok_dets = self.fp_filter.filter(dets)
            self._latest_detections = ok_dets
            if ok_dets:
                self._log(f"Detections: {len(ok_dets)}")

            try:
                backend = ""
                url = ""
                if self._preview_thread is not None:
                    backend = self._preview_thread.backend or ""
                    url = self._preview_thread.rtsp_url or ""
                self.indexer.add_frame(frame, ts, video_url=url, backend=backend)
            except Exception:
                logger.exception("Realtime indexing error")

        except Exception as e:
            logger.exception("Processing error: %s", e)

    # ═══════════════════════════════════════════════════════
    # YOLO 画框（保持不变）
    # ═══════════════════════════════════════════════════════

    _CLASS_COLORS = {
        0: (0, 255, 0), 1: (255, 255, 0), 2: (0, 165, 255),
        3: (255, 0, 255), 5: (0, 255, 255), 7: (128, 0, 128),
        9: (0, 140, 255), 14: (255, 255, 255), 15: (255, 0, 0),
        16: (0, 0, 255), 25: (128, 128, 128), 39: (0, 255, 128),
        41: (200, 100, 0), 56: (50, 50, 200), 63: (200, 200, 0),
        64: (180, 0, 180), 67: (100, 255, 100),
    }

    def _draw_boxes(self, frame: np.ndarray, detections: list):
        for d in detections:
            x1, y1, x2, y2 = int(d[0]), int(d[1]), int(d[2]), int(d[3])
            conf = float(d[4])
            cls_id = int(d[5])
            color = self._CLASS_COLORS.get(cls_id, (0, 255, 0))
            label = self._coco_names.get(cls_id, f"cls_{cls_id}")
            text = f"{label} {conf:.2f}"
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1 - th - 4), (x1 + tw, y1), color, -1)
            cv2.putText(frame, text, (x1, y1 - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    # ═══════════════════════════════════════════════════════
    # 搜索（保持不变）
    # ═══════════════════════════════════════════════════════

    def _on_search(self):
        text = self.search_input.text().strip()
        if not text:
            return
        self._cancel_search()
        topk = int(self.topk_combo.currentText())
        source = self.source_combo.currentText()
        source_key = "realtime" if source == "实时抽帧" else "history"
        self._log(f"搜索: '{text}' (源={source}, top_k={topk})")
        self._search_thread = SearchThread(parent=self)
        self._search_thread.search_started.connect(self._on_search_started)
        self._search_thread.search_finished.connect(self._on_search_finished)
        self._search_thread.search_error.connect(self._on_search_error)
        self._search_thread.setup(
            text=text, top_k=topk, source=source_key,
            frame=self._latest_frame, timestamp=self._latest_ts,
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
        self.btn_search.setText("\U0001f50d 搜索")

    @pyqtSlot()
    def _on_search_started(self):
        self._log("搜索进行中...")

    @pyqtSlot(list)
    def _on_search_finished(self, results: list):
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
            self.results_table.setItem(row, 2, QTableWidgetItem(f"{similarity:.3f}"))
            self.results_table.setItem(row, 3, QTableWidgetItem(
                str(meta.get("source", meta.get("channel", "")))))
            op_item = QTableWidgetItem("▶ 跳转播放")
            op_item.setData(Qt.UserRole, r)
            self.results_table.setItem(row, 4, op_item)
        self._search_thread = None
        self.btn_search.setEnabled(True)
        self.btn_search.setText("\U0001f50d 搜索")

    @pyqtSlot(str)
    def _on_search_error(self, error: str):
        self._log(f"搜索失败: {error}")
        self._search_thread = None
        self.btn_search.setEnabled(True)
        self.btn_search.setText("\U0001f50d 搜索")

    # ═══════════════════════════════════════════════════════
    # 索引构建（保持不变）
    # ═══════════════════════════════════════════════════════

    def _on_index_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", str(settings.VIDEOS_DIR),
            "Video Files (*.mp4 *.avi *.mov *.mkv)",
        )
        if not path:
            return
        self._cancel_index_build()
        self._log(f"开始构建视频索引: {path}")
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.btn_cancel_index.setVisible(True)
        self.btn_index_video.setEnabled(False)
        self._index_thread = IndexThread(parent=self)
        self._index_thread.progress_updated.connect(self._on_index_progress)
        self._index_thread.index_built.connect(self._on_index_built)
        self._index_thread.index_error.connect(self._on_index_error)
        self._index_thread.setup(video_path=path, sample_fps=settings.VIDEO_INDEX_SAMPLE_FPS)
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
        if total > 0:
            self.progress_bar.setValue(min(100, int(100 * current / total)))

    @pyqtSlot(str)
    def _on_index_built(self, index_path: str):
        self._log(f"视频索引构建完成: {index_path}")
        self._finish_index_build()

    @pyqtSlot(str)
    def _on_index_error(self, error: str):
        self._log(f"视频索引构建失败: {error}")
        self._finish_index_build()

    def _finish_index_build(self):
        self._index_thread = None
        self.progress_bar.setVisible(False)
        self.btn_cancel_index.setVisible(False)
        self.btn_index_video.setEnabled(True)

    # ═══════════════════════════════════════════════════════
    # 结果双击
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
    # 词库看板
    # ═══════════════════════════════════════════════════════

    def _on_open_vocabulary(self):
        """打开文搜图词库看板"""
        panel = SearchVocabularyPanel(self)
        panel.word_selected.connect(self._on_vocabulary_word_selected)
        panel.search_requested.connect(self._on_vocabulary_search)
        panel.exec_()

    def _on_vocabulary_word_selected(self, word: str):
        """词库面板中点击了某个词 — 词库面板内部已更新选中列表，此处仅转发"""
        _ = word  # 词库面板内部处理，此处不需要额外操作

    def _on_vocabulary_search(self, text: str):
        """词库面板点击搜索 → 填入搜索框 → 触发搜索"""
        self.search_input.setText(text)
        self._on_search()

    # ═══════════════════════════════════════════════════════
    # 导出
    # ═══════════════════════════════════════════════════════

    def _on_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "results.csv", "CSV Files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["#", "时间戳", "相似度", "源"])
            for r in range(self.results_table.rowCount()):
                idx = (self.results_table.item(r, 0).text()
                       if self.results_table.item(r, 0) else "")
                ts = (self.results_table.item(r, 1).text()
                      if self.results_table.item(r, 1) else "")
                sim = (self.results_table.item(r, 2).text()
                       if self.results_table.item(r, 2) else "")
                src = (self.results_table.item(r, 3).text()
                       if self.results_table.item(r, 3) else "")
                writer.writerow([idx, ts, sim, src])
        self._log(f"搜索结果已导出: {path}")

    # ═══════════════════════════════════════════════════════
    # FTP 监控（保持不变）
    # ═══════════════════════════════════════════════════════

    def _start_ftp_monitoring(self):
        if self._ftp_monitoring:
            return
        watch_dir = self.ftp_dir_input.text().strip()
        if not watch_dir or not os.path.isdir(watch_dir):
            self._log(f"FTP 监控目录不存在: {watch_dir}")
            self.ftp_status_lbl.setText("❌ 目录不存在")
            return

        self._ftp_monitor = FTPMonitor(watch_dir=watch_dir, parent=self)
        self._ftp_monitor.file_detected.connect(self._on_ftp_file_detected)
        self._ftp_monitor.monitor_error.connect(self._on_ftp_monitor_error)
        self._ftp_monitor.monitor_status.connect(self._on_ftp_monitor_status)
        self._ftp_monitor.start()

        profile_path = str(settings.NVR_PROFILE_PATH)
        self._verification_worker = VerificationWorker(
            detector=self.detector, profile_path=profile_path, parent=self)
        self._verification_worker.verification_complete.connect(
            self._on_verification_complete)
        self._verification_worker.verification_error.connect(
            self._on_verification_error)
        self._verification_worker.worker_status.connect(
            lambda s: self.ftp_queue_lbl.setText(s))
        self._verification_worker.start()

        self._ftp_monitoring = True
        self.btn_ftp_start.setEnabled(False)
        self.btn_ftp_stop.setEnabled(True)
        self.ftp_status_lbl.setText("\U0001f7e2 监控中")
        self._log(f"FTP 监控已启动: {watch_dir}")

    def _stop_ftp_monitoring(self):
        if not self._ftp_monitoring:
            return
        self._ftp_monitoring = False
        if self._ftp_monitor is not None:
            fm = self._ftp_monitor
            fm.stop()
            fm.quit()
            if not fm.wait(5000):
                logger.warning("FTPMonitor did not finish in 5s")
                fm.terminate()
                fm.wait(3000)
            self._ftp_monitor = None
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

    @pyqtSlot(dict)
    def _on_ftp_file_detected(self, record: dict):
        self._log(f"FTP: {record.get('original', '?')} "
                  f"ch={record.get('channel', '?')} "
                  f"type={record.get('alarm_type', '?')}")
        self.alert_panel.add_pending(record)
        if self._verification_worker:
            self._verification_worker.enqueue(record)
            pending = self._verification_worker.pending_count
            self.ftp_queue_lbl.setText(f"待处理: {pending}")

    @pyqtSlot(dict)
    def _on_verification_complete(self, result: dict):
        filename = result.get("filename", "")
        self.alert_panel.update_alert(filename, result)
        self.alert_panel.refresh_report()
        # ★ 追加到配置测试器
        if self.config_test_widget:
            self.config_test_widget.add_result(result)
        if self._verification_worker:
            pending = self._verification_worker.pending_count
            self.ftp_queue_lbl.setText(f"待处理: {pending}" if pending > 0 else "✅ 全部完成")
        is_false = result.get("is_false_alarm", False)
        status_text = "⚠️误报" if is_false else "✅正常"
        self._log(f"核验: {filename} → {status_text} "
                  f"(置信度={result.get('yolo_max_confidence', 0):.2f})")

    @pyqtSlot(str, str)
    def _on_verification_error(self, filepath: str, error: str):
        self._log(f"核验错误: {filepath} — {error}")

    @pyqtSlot(str)
    def _on_ftp_monitor_error(self, error: str):
        self._log(f"FTP 监控错误: {error}")
        self.ftp_status_lbl.setText("❌ 错误")

    @pyqtSlot(str)
    def _on_ftp_monitor_status(self, status: str):
        self._log(f"FTP: {status}")

    # ═══════════════════════════════════════════════════════
    # 运行时 + 日志
    # ═══════════════════════════════════════════════════════

    def _update_runtime(self):
        secs = int(self._elapsed.elapsed() / 1000) if self._running else 0
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        self.runtime_lbl.setText(f"运行时间: {h:02d}:{m:02d}:{s:02d}")

    def _log(self, text: str):
        self.log_widget.append(text)
