"""
audio_detector.py — NVR 音频检测引擎

检测 NVR 视频文件/RTSP 流中的音频参数，生成量化检测报告。

支持两种模式：
1. detect_file(video_path) — 对 MP4 文件做完整音频分析
2. detect_stream(rtsp_url, duration) — 对 RTSP 流采样 N 秒分析

核心流程：
    ffprobe 探测音频流信息
    → (有音频) ffmpeg 提取 PCM 16-bit mono
    → numpy 逐帧 RMS 分析
    → 生成 AudioReport

依赖：系统需安装 ffmpeg/ffprobe。
"""

import subprocess
import tempfile
import os
import json
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

import numpy as np

from utils.logger import get_logger

logger = get_logger("audio_detector")

# PCM 采样配置
PCM_SAMPLE_RATE = 16000   # 统一重采样到 16kHz 分析
PCM_FORMAT = "s16le"       # 16-bit signed little-endian
FRAME_SIZE = 1024          # 每帧样本数 (~64ms @ 16kHz)
SILENCE_THRESHOLD_DBFS = -50.0  # 静音阈值
CLIPPING_THRESHOLD = 0.98       # 削波阈值（接近 1.0 = 0 dBFS）


@dataclass
class AudioReport:
    """NVR 音频检测报告"""

    # ── 基础信息 ────────────────────────────────────
    source: str = ""                # 文件路径或 RTSP URL
    has_audio: bool = False         # 是否包含音频轨道
    codec: str = ""                 # 编码格式 (aac/pcm_mulaw/...)
    codec_long: str = ""            # 编码全称
    sample_rate: int = 0            # 原始采样率 (Hz)
    channels: int = 0               # 声道数
    bit_rate: int = 0               # 比特率 (bps)
    duration_sec: float = 0.0       # 音频时长 (秒)

    # ── 质量指标 ────────────────────────────────────
    rms_dbfs: float = -100.0        # 平均音量 (dBFS, 越接近0越大声)
    peak_dbfs: float = -100.0       # 峰值电平 (dBFS)
    noise_floor_dbfs: float = -100.0 # 底噪水平 (dBFS)
    snr_db: float = 0.0             # 信噪比 (dB, 越高越好)
    clipping_ratio: float = 0.0     # 削波比例 (0~1)
    silence_ratio: float = 0.0      # 静音比例 (0~1)
    loudness_range: float = 0.0     # 响度动态范围 (dB)

    # ── 连续性 ──────────────────────────────────────
    dropouts: int = 0               # 断流次数
    av_sync_offset_ms: float = 0.0  # 音视频同步偏移 (ms)

    # ── 总体判定 ────────────────────────────────────
    is_normal: bool = True          # 综合判定
    issues: List[str] = field(default_factory=list)  # 问题列表
    processing_time_sec: float = 0.0

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "has_audio": self.has_audio,
            "codec": self.codec,
            "codec_long": self.codec_long,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bit_rate": self.bit_rate,
            "bit_rate_kbps": round(self.bit_rate / 1000, 1) if self.bit_rate else 0,
            "duration_sec": round(self.duration_sec, 2),
            "rms_dbfs": round(self.rms_dbfs, 1),
            "peak_dbfs": round(self.peak_dbfs, 1),
            "noise_floor_dbfs": round(self.noise_floor_dbfs, 1),
            "snr_db": round(self.snr_db, 1),
            "clipping_ratio": round(self.clipping_ratio * 100, 2),
            "silence_ratio": round(self.silence_ratio * 100, 2),
            "loudness_range": round(self.loudness_range, 1),
            "dropouts": self.dropouts,
            "av_sync_offset_ms": round(self.av_sync_offset_ms, 1),
            "is_normal": self.is_normal,
            "issues": self.issues,
            "processing_time_sec": round(self.processing_time_sec, 2),
        }

    def summary(self) -> str:
        """单行文字摘要"""
        if not self.has_audio:
            return "[NO_AUDIO]"
        status = "OK" if self.is_normal else "WARN"
        return (
            f"[{status}] {self.codec} {self.sample_rate}Hz "
            f"{self.channels}ch "
            f"RMS={self.rms_dbfs:.1f}dBFS "
            f"SNR={self.snr_db:.1f}dB"
        )


class AudioDetector:
    """
    NVR 音频检测器。

    用法:
        detector = AudioDetector()
        report = detector.detect_file("path/to/video.mp4")
        print(report.summary())
    """

    def __init__(self):
        self._check_ffmpeg()

    def _check_ffmpeg(self):
        """检查 ffmpeg/ffprobe 是否可用"""
        self._ffprobe_path = self._find_binary("ffprobe")
        self._ffmpeg_path = self._find_binary("ffmpeg")

        if not self._ffprobe_path:
            logger.warning("ffprobe 未找到，音频探测不可用")
        if not self._ffmpeg_path:
            logger.warning("ffmpeg 未找到，音频提取不可用")

    @staticmethod
    def _find_binary(name: str) -> Optional[str]:
        """跨平台查找可执行文件路径"""
        import shutil
        import os as _os
        import platform

        # 1. PATH 查找
        p = shutil.which(name) or shutil.which(name + ".exe")
        if p:
            return p

        system = platform.system()

        # 2. Windows 常见安装路径
        if system == "Windows":
            for base in [
                _os.environ.get("ProgramFiles", r"C:\Program Files"),
                _os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            ]:
                candidate = _os.path.join(base, "ffmpeg", "bin", name + ".exe")
                if _os.path.exists(candidate):
                    return candidate

        # 3. Linux 常见路径
        if system == "Linux":
            for p in [f"/usr/bin/{name}", f"/usr/local/bin/{name}"]:
                if _os.path.exists(p):
                    return p

        # 4. macOS Homebrew
        if system == "Darwin":
            p = f"/opt/homebrew/bin/{name}"
            if _os.path.exists(p):
                return p

        return None

    # ── 公共接口 ──────────────────────────────────────

    def detect_file(self, video_path: str) -> AudioReport:
        """对 MP4/视频文件做完整音频检测"""
        t0 = time.time()
        report = AudioReport(source=video_path)

        if not os.path.exists(video_path):
            report.issues.append("文件不存在")
            report.is_normal = False
            return report

        # 1. ffprobe 探测音频流
        probe = self._probe_audio_stream(video_path)
        if probe is None:
            report.issues.append("ffprobe 探测失败")
            report.is_normal = False
            report.processing_time_sec = time.time() - t0
            return report

        if not probe.get("has_audio"):
            report.processing_time_sec = time.time() - t0
            return report

        # 填充基础信息
        report.has_audio = True
        report.codec = probe.get("codec_name", "unknown")
        report.codec_long = probe.get("codec_long_name", "")
        report.sample_rate = int(probe.get("sample_rate", 0))
        report.channels = int(probe.get("channels", 0))
        report.bit_rate = int(probe.get("bit_rate", 0))
        report.duration_sec = float(probe.get("duration", 0))

        # 2. 提取 PCM 并分析质量
        pcm_data = self._extract_pcm(video_path)
        if pcm_data is not None and len(pcm_data) > 0:
            self._analyze_pcm(pcm_data, report)

        # 3. 综合判定
        self._judge(report)

        report.processing_time_sec = time.time() - t0
        logger.info("音频检测完成: %s", report.summary())
        return report

    def detect_stream(self, rtsp_url: str, duration: float = 15.0) -> AudioReport:
        """对 RTSP 流采样音频检测"""
        t0 = time.time()
        report = AudioReport(source=rtsp_url)

        # 1. ffprobe 探测
        probe = self._probe_audio_stream(rtsp_url)
        if probe is None:
            report.issues.append("ffprobe 探测失败（可能流不可达）")
            report.is_normal = False
            report.processing_time_sec = time.time() - t0
            return report

        if not probe.get("has_audio"):
            report.processing_time_sec = time.time() - t0
            return report

        report.has_audio = True
        report.codec = probe.get("codec_name", "unknown")
        report.codec_long = probe.get("codec_long_name", "")
        report.sample_rate = int(probe.get("sample_rate", 0))
        report.channels = int(probe.get("channels", 0))
        report.bit_rate = int(probe.get("bit_rate", 0))

        # 2. 采样提取 PCM (限定时长)
        pcm_data = self._extract_pcm(rtsp_url, duration=duration)
        if pcm_data is not None and len(pcm_data) > 0:
            self._analyze_pcm(pcm_data, report)
            report.duration_sec = len(pcm_data) / PCM_SAMPLE_RATE / 2  # 16-bit = 2 bytes

        self._judge(report)
        report.processing_time_sec = time.time() - t0
        logger.info("RTSP 音频采样完成: %s", report.summary())
        return report

    # ── ffprobe 探测 ─────────────────────────────────

    def _probe_audio_stream(self, input_path: str) -> Optional[dict]:
        """使用 ffprobe 探测音频流信息"""
        if not self._ffprobe_path:
            return None

        cmd = [
            self._ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "a",  # 只选音频流
            input_path,
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode != 0:
                logger.debug("ffprobe error: %s", result.stderr[:200])
                return None

            data = json.loads(result.stdout)
            streams = data.get("streams", [])

            if not streams:
                return {"has_audio": False}

            audio = streams[0]
            audio["has_audio"] = True
            return audio

        except subprocess.TimeoutExpired:
            logger.warning("ffprobe 超时: %s", input_path)
            return None
        except json.JSONDecodeError:
            logger.warning("ffprobe 输出非 JSON: %s", input_path)
            return None
        except Exception:
            logger.exception("ffprobe 异常: %s", input_path)
            return None

    # ── PCM 提取 ────────────────────────────────────

    def _extract_pcm(
        self, input_path: str, duration: Optional[float] = None
    ) -> Optional[bytes]:
        """使用 ffmpeg 提取音频为 PCM 16-bit mono 16kHz"""
        if not self._ffmpeg_path:
            return None

        cmd = [
            self._ffmpeg_path,
            "-v", "quiet",
            "-i", input_path,
            "-vn",                           # 不要视频
            "-acodec", "pcm_s16le",          # PCM 16-bit
            "-ac", "1",                       # 单声道
            "-ar", str(PCM_SAMPLE_RATE),     # 重采样到 16kHz
            "-f", "s16le",                   # raw PCM
            "-",                              # 输出到 stdout
        ]

        if duration is not None:
            cmd.insert(-4, "-t")
            cmd.insert(-4, str(duration))

        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

            if result.returncode != 0:
                stderr = result.stderr.decode(errors="ignore")[:200]
                logger.debug("ffmpeg PCM 提取失败: %s", stderr)
                return None

            return result.stdout

        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg PCM 提取超时: %s", input_path)
            return None
        except Exception:
            logger.exception("ffmpeg PCM 异常: %s", input_path)
            return None

    # ── PCM 分析 ────────────────────────────────────

    def _analyze_pcm(self, pcm_data: bytes, report: AudioReport):
        """分析 PCM 数据，填充报告质量指标"""
        try:
            # 将 bytes 转为 numpy int16 数组
            samples = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float64)
            if len(samples) == 0:
                return

            # 归一化到 [-1, 1]
            samples /= 32768.0

            total_frames = len(samples) // FRAME_SIZE
            if total_frames < 2:
                logger.debug("PCM 数据不足: %d 样本", len(samples))
                return

            # 逐帧 RMS
            rms_values = []
            for i in range(total_frames):
                start = i * FRAME_SIZE
                end = start + FRAME_SIZE
                frame = samples[start:end]

                rms = np.sqrt(np.mean(frame ** 2))
                rms_values.append(rms)

            rms_array = np.array(rms_values)

            # RMS → dBFS: 20 * log10(rms)
            with np.errstate(divide="ignore"):
                dbfs_array = 20 * np.log10(rms_array + 1e-12)

            # 统计
            report.rms_dbfs = float(np.mean(dbfs_array))
            report.peak_dbfs = float(np.max(dbfs_array))

            # 底噪：最低 10% 帧的 RMS 均值
            sorted_dbfs = np.sort(dbfs_array)
            noise_count = max(1, int(len(sorted_dbfs) * 0.1))
            report.noise_floor_dbfs = float(np.mean(sorted_dbfs[:noise_count]))

            # 信噪比
            report.snr_db = round(report.peak_dbfs - report.noise_floor_dbfs, 1)

            # 响度范围
            p10 = float(np.percentile(dbfs_array, 10))
            p90 = float(np.percentile(dbfs_array, 90))
            report.loudness_range = round(p90 - p10, 1)

            # 削波检测
            clipping_samples = np.sum(np.abs(samples) >= CLIPPING_THRESHOLD)
            report.clipping_ratio = float(clipping_samples / len(samples))

            # 静音帧比例
            silence_frames = np.sum(dbfs_array < SILENCE_THRESHOLD_DBFS)
            report.silence_ratio = float(silence_frames / total_frames)

            # 断流检测：连续静音帧 > 2 秒 (~31 帧 @ 64ms/帧)
            dropout_threshold = int(2.0 / (FRAME_SIZE / PCM_SAMPLE_RATE))
            silent = dbfs_array < SILENCE_THRESHOLD_DBFS
            consecutive = 0
            for is_silent in silent:
                if is_silent:
                    consecutive += 1
                else:
                    if consecutive >= dropout_threshold:
                        report.dropouts += 1
                    consecutive = 0
            if consecutive >= dropout_threshold:
                report.dropouts += 1

        except Exception:
            logger.exception("PCM 分析异常")

    # ── 综合判定 ────────────────────────────────────

    def _judge(self, report: AudioReport):
        """生成综合判定"""
        issues = []

        if not report.has_audio:
            issues.append("未检测到音频轨道")
            report.is_normal = False
            report.issues = issues
            return

        # 编码检查
        if report.codec == "unknown":
            issues.append("音频编码未知")

        # 采样率检查
        if report.sample_rate and report.sample_rate < 8000:
            issues.append(f"采样率过低: {report.sample_rate}Hz")

        # 静音检查
        if report.silence_ratio > 0.9:
            issues.append(f"静音比例过高: {report.silence_ratio*100:.0f}%")
        elif report.silence_ratio > 0.5:
            issues.append(f"静音比例偏高: {report.silence_ratio*100:.0f}%")

        # SNR 检查
        if report.snr_db < 6:
            issues.append(f"信噪比过低: {report.snr_db:.1f}dB")

        # 削波检查
        if report.clipping_ratio > 0.05:
            issues.append(f"削波严重: {report.clipping_ratio*100:.1f}% 样本削波")

        # 断流检查
        if report.dropouts > 0:
            issues.append(f"检测到 {report.dropouts} 次音频断流")

        # RMS 过低
        if report.rms_dbfs < -60:
            issues.append(f"音量过低: {report.rms_dbfs:.0f}dBFS")

        report.issues = issues
        report.is_normal = len(issues) == 0
