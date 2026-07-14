"""
audio_test_widget.py — 音频检测 GUI 面板

支持两种模式：
1. 文件检测：选择 MP4 视频文件 → 自动分析音频参数
2. RTSP 流检测：输入 RTSP URL → 采样 15 秒分析

展示：仪表盘式结果展示 + 参数表格
"""

import os
import threading
from pathlib import Path

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QGroupBox, QGridLayout, QProgressBar,
    QFileDialog, QMessageBox, QFrame, QScrollArea,
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor

from core.audio_detector import AudioDetector, AudioReport
from utils.logger import get_logger

logger = get_logger("audio_test_widget")

# 参数状态颜色
COLOR_NORMAL = "#4CAF50"
COLOR_WARN = "#FF9800"
COLOR_ERROR = "#f44336"
COLOR_INACTIVE = "#bdbdbd"


class ParamRow(QFrame):
    """单行参数显示：标签 + 值 + 状态灯"""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("QFrame { background: #fafafa; border-radius: 4px; padding: 4px; }")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        self._label = QLabel(label)
        self._label.setStyleSheet("color: #555; font-size: 12px;")
        self._label.setMinimumWidth(80)
        layout.addWidget(self._label)

        self._value = QLabel("—")
        self._value.setStyleSheet("font-weight: bold; font-size: 13px; color: #333;")
        layout.addWidget(self._value, stretch=1)

        self._status = QLabel("")
        self._status.setFixedSize(12, 12)
        self._status.setStyleSheet(f"background: {COLOR_INACTIVE}; border-radius: 6px;")
        layout.addWidget(self._status)

    def set_value(self, text: str, status: str = "normal"):
        """设置值 + 状态灯颜色 (normal/warn/error/inactive)"""
        self._value.setText(text)
        color_map = {
            "normal": COLOR_NORMAL,
            "warn": COLOR_WARN,
            "error": COLOR_ERROR,
            "inactive": COLOR_INACTIVE,
        }
        self._status.setStyleSheet(
            f"background: {color_map.get(status, COLOR_INACTIVE)}; border-radius: 6px;"
        )


class AudioTestWidget(QWidget):
    """音频检测面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._detector: AudioDetector = AudioDetector()
        self._report: AudioReport = None
        self._running = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── 标题 ──────────────────────────────────────
        title = QLabel("🎤 NVR 音频检测")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        layout.addWidget(title)

        desc = QLabel("检测 NVR 视频文件或 RTSP 流中的音频参数\n支持 MP4/AVI 文件分析和 RTSP 实时流采样")
        desc.setStyleSheet("color: #777; font-size: 11px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # ── 文件检测区 ────────────────────────────────
        file_group = QGroupBox("📁 文件检测")
        file_layout = QHBoxLayout(file_group)

        self.file_path_input = QLineEdit()
        self.file_path_input.setPlaceholderText("选择 MP4/AVI 视频文件...")
        file_layout.addWidget(self.file_path_input, stretch=1)

        self.btn_browse = QPushButton("📂 浏览")
        self.btn_browse.clicked.connect(self._on_browse_file)
        file_layout.addWidget(self.btn_browse)

        self.btn_detect_file = QPushButton("🔍 检测文件")
        self.btn_detect_file.clicked.connect(self._on_detect_file)
        file_layout.addWidget(self.btn_detect_file)

        layout.addWidget(file_group)

        # ── 流检测区 ──────────────────────────────────
        stream_group = QGroupBox("📡 RTSP 流检测")
        stream_layout = QHBoxLayout(stream_group)

        self.rtsp_input = QLineEdit()
        self.rtsp_input.setPlaceholderText("输入 RTSP URL (如 rtsp://192.168.1.x:554/...)")
        stream_layout.addWidget(self.rtsp_input, stretch=1)

        self.btn_detect_stream = QPushButton("🔍 采样检测 (15秒)")
        self.btn_detect_stream.clicked.connect(self._on_detect_stream)
        stream_layout.addWidget(self.btn_detect_stream)

        layout.addWidget(stream_group)

        # ── 进度条 ────────────────────────────────────
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # 不确定模式
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # ── 结果区域 ──────────────────────────────────
        result_label = QLabel("检测结果")
        result_label.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        layout.addWidget(result_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        result_widget = QWidget()
        self.result_layout = QVBoxLayout(result_widget)
        self.result_layout.setSpacing(6)

        # 1. 基础信息组
        self._setup_param_group("📋 基础信息", [
            "音频状态", "编码格式", "编码全称", "采样率", "声道数", "比特率", "音频时长",
        ])
        self.basic_params: dict[str, ParamRow] = {}

        # 创建参数行
        for name in ["音频状态", "编码格式", "编码全称", "采样率", "声道数", "比特率", "音频时长"]:
            row = ParamRow(name)
            self.result_layout.addWidget(row)
            self.basic_params[name] = row

        # 分隔
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setStyleSheet("color: #ddd;")
        self.result_layout.addWidget(sep1)

        # 2. 质量参数组
        quality_label = QLabel("📊 音频质量")
        quality_label.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        self.result_layout.addWidget(quality_label)

        self.quality_params: dict[str, ParamRow] = {}
        for name in ["平均音量", "峰值电平", "底噪水平", "信噪比(SNR)", "削波比例", "静音比例", "响度范围"]:
            row = ParamRow(name)
            self.result_layout.addWidget(row)
            self.quality_params[name] = row

        # 分隔
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #ddd;")
        self.result_layout.addWidget(sep2)

        # 3. 连续性
        cont_label = QLabel("🔗 连续性")
        cont_label.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        self.result_layout.addWidget(cont_label)

        self.cont_params: dict[str, ParamRow] = {}
        for name in ["断流次数", "音视频同步"]:
            row = ParamRow(name)
            self.result_layout.addWidget(row)
            self.cont_params[name] = row

        # 分隔
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.HLine)
        sep3.setStyleSheet("color: #ddd;")
        self.result_layout.addWidget(sep3)

        # 4. 总体判定
        self.overall_status = QLabel("等待检测...")
        self.overall_status.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        self.overall_status.setStyleSheet("color: #999; padding: 8px;")
        self.result_layout.addWidget(self.overall_status)

        self.issues_label = QLabel("")
        self.issues_label.setWordWrap(True)
        self.issues_label.setStyleSheet("color: #f44336; font-size: 12px;")
        self.result_layout.addWidget(self.issues_label)

        self.result_layout.addStretch()
        scroll.setWidget(result_widget)
        layout.addWidget(scroll, stretch=1)

    def _setup_param_group(self, title: str, param_names: list):
        """预留参数组接口"""
        pass

    # ── 动作 ──────────────────────────────────────────

    def _on_browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "",
            "视频文件 (*.mp4 *.avi *.mkv *.mov);;所有文件 (*.*)"
        )
        if path:
            self.file_path_input.setText(path)

    def _on_detect_file(self):
        """检测文件音频"""
        path = self.file_path_input.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "错误", "请选择有效的视频文件")
            return

        self._run_detection(lambda: self._detector.detect_file(path))

    def _on_detect_stream(self):
        """检测 RTSP 流音频"""
        url = self.rtsp_input.text().strip()
        if not url:
            QMessageBox.warning(self, "错误", "请输入 RTSP URL")
            return

        self._run_detection(lambda: self._detector.detect_stream(url, duration=15.0))

    def _run_detection(self, detect_func):
        """在后台线程中运行检测"""
        if self._running:
            return

        self._running = True
        self.progress_bar.setVisible(True)
        self.btn_detect_file.setEnabled(False)
        self.btn_detect_stream.setEnabled(False)

        def worker():
            try:
                report = detect_func()
                self._report = report
                # 在主线程更新 UI
                QTimer.singleShot(0, lambda: self._display_report(report))
            except Exception as e:
                logger.exception("音频检测异常")
                QTimer.singleShot(0, lambda: self._on_error(str(e)))
            finally:
                QTimer.singleShot(0, self._on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _display_report(self, report: AudioReport):
        """显示检测报告"""
        d = report.to_dict()

        # ── 基础信息 ──────────────────────────────────
        self.basic_params["音频状态"].set_value(
            "✅ 有音频" if d["has_audio"] else "❌ 无音频",
            "normal" if d["has_audio"] else "error"
        )
        self.basic_params["编码格式"].set_value(
            d["codec"] or "—",
            "normal" if d["codec"] else "inactive"
        )
        self.basic_params["编码全称"].set_value(d["codec_long"] or "—")
        self.basic_params["采样率"].set_value(
            f"{d['sample_rate']} Hz" if d["sample_rate"] else "—",
            "warn" if (d["sample_rate"] and d["sample_rate"] < 8000) else "normal"
        )
        self.basic_params["声道数"].set_value(
            f"{d['channels']} (单声道)" if d["channels"] == 1
            else f"{d['channels']} (立体声)" if d["channels"] == 2
            else str(d["channels"])
        )
        self.basic_params["比特率"].set_value(
            f"{d['bit_rate_kbps']} kbps" if d["bit_rate"] else "—"
        )
        self.basic_params["音频时长"].set_value(f"{d['duration_sec']}s" if d["duration_sec"] else "—")

        # ── 质量 ──────────────────────────────────────
        rms = d["rms_dbfs"]
        rms_status = "normal" if rms > -60 else ("warn" if rms > -80 else "error")
        self.quality_params["平均音量"].set_value(f"{rms:.1f} dBFS", rms_status)

        peak = d["peak_dbfs"]
        self.quality_params["峰值电平"].set_value(f"{peak:.1f} dBFS",
            "error" if peak > -1 else "normal")

        noise = d["noise_floor_dbfs"]
        self.quality_params["底噪水平"].set_value(f"{noise:.1f} dBFS",
            "warn" if noise > -40 else "normal")

        snr = d["snr_db"]
        snr_status = "normal" if snr >= 12 else ("warn" if snr >= 6 else "error")
        self.quality_params["信噪比(SNR)"].set_value(f"{snr:.1f} dB", snr_status)

        clip = d["clipping_ratio"]
        clip_status = "normal" if clip < 1 else ("warn" if clip < 5 else "error")
        self.quality_params["削波比例"].set_value(f"{clip:.2f}%", clip_status)

        silence = d["silence_ratio"]
        sil_status = "normal" if silence < 10 else ("warn" if silence < 50 else "error")
        self.quality_params["静音比例"].set_value(f"{silence:.1f}%", sil_status)

        self.quality_params["响度范围"].set_value(f"{d['loudness_range']:.1f} dB")

        # ── 连续性 ────────────────────────────────────
        drop_status = "normal" if d["dropouts"] == 0 else "error"
        self.cont_params["断流次数"].set_value(str(d["dropouts"]), drop_status)

        sync = d["av_sync_offset_ms"]
        sync_status = "normal" if abs(sync) < 200 else "warn"
        self.cont_params["音视频同步"].set_value(f"{sync:.1f} ms", sync_status)

        # ── 总体判定 ──────────────────────────────────
        if d["is_normal"]:
            self.overall_status.setText("✅ 音频正常")
            self.overall_status.setStyleSheet(f"color: {COLOR_NORMAL}; font-size: 14px; font-weight: bold; padding: 8px;")
        else:
            self.overall_status.setText("⚠️ 检测到音频问题")
            self.overall_status.setStyleSheet(f"color: {COLOR_ERROR}; font-size: 14px; font-weight: bold; padding: 8px;")

        if report.issues:
            self.issues_label.setText("问题: " + " | ".join(report.issues))
        else:
            self.issues_label.setText("")

    def _on_error(self, msg: str):
        QMessageBox.critical(self, "检测错误", f"音频检测失败:\n{msg}")

    def _on_done(self):
        self._running = False
        self.progress_bar.setVisible(False)
        self.btn_detect_file.setEnabled(True)
        self.btn_detect_stream.setEnabled(True)
