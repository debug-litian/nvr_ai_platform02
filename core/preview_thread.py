"""
PreviewThread — RTSP 拉流与解码线程

继承 QThread，独立运行，负责：
- RTSP 拉流（OpenCV / ffmpeg 子进程解码）
- 坏帧过滤与重连
- CPU 软解码 / GPU 硬解码模式切换
- 通过信号将帧发送到主线程

信号：
- frame_ready(np.ndarray): 发送当前帧（深拷贝）到主线程显示
- status_updated(str): 更新连接状态
- fps_updated(float): 实际拉流帧率
"""
import time
import os
import subprocess
import queue
import numpy as np
import cv2
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from config import settings
from utils.logger import get_logger

logger = get_logger("preview_thread")


class PreviewThread(QThread):
    """RTSP 拉流与解码线程"""

    # === 信号定义 ===
    frame_ready = pyqtSignal(np.ndarray)   # 当前帧（已深拷贝）
    status_updated = pyqtSignal(str)        # "已连接" / "断开" / "重连中..."
    fps_updated = pyqtSignal(float)         # 实际拉流帧率

    def __init__(self, rtsp_url: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.rtsp_url = rtsp_url or settings.RTSP_URL
        self.cap: Optional[cv2.VideoCapture] = None  # type: ignore
        self._stopped = False
        self._backend = ""
        self._decode_mode = "cpu"  # "cpu" | "gpu"
        self._last_success_time = 0.0

        # ffmpeg 子进程相关
        self.ffmpeg_proc = None
        self._ffmpeg_frame_bytes = 0
        self._ffmpeg_size = (
            tuple(settings.FFMPEG_DECODE_SIZE)
            if hasattr(settings, "FFMPEG_DECODE_SIZE")
            else (640, 480)
        )

        # 帧队列：拉流线程写入，主线程通过 read() 取（也可直接用信号）
        self._frame_queue = queue.Queue(maxsize=4)

        # 帧率统计
        self._frame_count = 0
        self._fps_last_ts = time.time()
        self._current_fps = 0.0

    # ── 公共接口 ──────────────────────────────────────

    def set_rtsp_url(self, url: str):
        """切换 RTSP 地址（需 stop 后调用）"""
        self.rtsp_url = url

    def set_decode_mode(self, mode: str):
        """
        切换解码模式。
        - "cpu": 使用 OpenCV 软解码
        - "gpu": 尝试 ffmpeg 硬件加速解码（如果配置了 USE_FFMPEG_HWACCEL）
        """
        if mode not in ("cpu", "gpu"):
            logger.warning("Unknown decode mode: %s, fallback to cpu", mode)
            mode = "cpu"
        if self._decode_mode != mode:
            self._decode_mode = mode
            logger.info("Decode mode switched to %s, will reopen capture", mode)
            # 如果线程正在运行，触发重连
            if self.isRunning():
                self._reopen_capture()

    def stop(self):
        """
        优雅停止：设置标志 → 等待循环退出 → 释放资源。
        主线程调用此方法，然后调用 quit() + wait()。
        """
        self._stopped = True
        logger.info("PreviewThread stop requested")

    def read_frame(self, timeout: float = 0.05) -> Optional[tuple]:
        """
        从内部队列读取 (timestamp, frame)。
        供主线程 QTimer 轮询使用（备选方案，推荐使用 frame_ready 信号）。
        """
        try:
            return self._frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # ── QThread 生命周期 ──────────────────────────────

    def run(self):
        """QThread 主循环"""
        self._stopped = False
        consecutive_bad = 0
        last_read_time = time.time()

        try:
            self._open_capture()

            while not self._stopped:
                ret, frame = self._read_one_frame()

                now = time.time()

                if not ret or frame is None:
                    # 长时间无帧 → 重连
                    if now - last_read_time > 5.0:
                        logger.warning(
                            "No frames for %.1fs, reopening capture", now - last_read_time
                        )
                        self._open_capture()
                        last_read_time = now
                    time.sleep(0.05)
                    continue

                self._last_success_time = now
                last_read_time = now

                # ── 帧验证 ──────────────────────────
                try:
                    if not isinstance(frame, np.ndarray):
                        continue
                    if frame.size == 0:
                        continue
                    if frame.ndim == 3 and frame.shape[2] not in (3, 4):
                        continue
                except Exception:
                    pass

                # 坏帧检测（灰度标准差）
                try:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    std = float(np.std(gray))
                    if std < settings.BAD_FRAME_STD_THRESHOLD:
                        consecutive_bad += 1
                        if consecutive_bad >= settings.MAX_CONSECUTIVE_BAD_FRAMES:
                            logger.warning("Too many consecutive bad frames, reopening")
                            self._open_capture()
                            consecutive_bad = 0
                        continue
                    else:
                        consecutive_bad = 0
                except Exception:
                    consecutive_bad = 0

                # ── 深拷贝后发射信号 ──────────────────
                try:
                    frame_copy = frame.copy()
                except Exception:
                    frame_copy = frame

                # 放入内部队列（供 QTimer 轮询）
                try:
                    if self._frame_queue.full():
                        try:
                            self._frame_queue.get_nowait()
                        except queue.Empty:
                            pass
                    self._frame_queue.put_nowait((now, frame_copy))
                except queue.Full:
                    pass

                # 发射信号给主线程
                self.frame_ready.emit(frame_copy)

                # 帧率统计
                self._frame_count += 1
                if now - self._fps_last_ts >= 1.0:
                    self._current_fps = self._frame_count / (now - self._fps_last_ts)
                    self._frame_count = 0
                    self._fps_last_ts = now
                    self.fps_updated.emit(self._current_fps)

        except Exception as e:
            logger.exception("PreviewThread fatal error: %s", e)
        finally:
            self._release_resources()
            logger.info("PreviewThread 线程已退出")

    # ── 私有方法 ──────────────────────────────────────

    def _read_one_frame(self) -> tuple:
        """读取一帧，返回 (ret, frame)"""
        # ffmpeg 子进程模式
        if (
            getattr(settings, "USE_FFMPEG_DECODE", False)
            and self.ffmpeg_proc is not None
        ):
            try:
                frame_bytes = self.ffmpeg_proc.stdout.read(self._ffmpeg_frame_bytes)
                if not frame_bytes or len(frame_bytes) < self._ffmpeg_frame_bytes:
                    logger.debug(
                        "ffmpeg incomplete frame (len=%s)",
                        len(frame_bytes) if frame_bytes else 0,
                    )
                    time.sleep(0.5)
                    self._start_ffmpeg_proc()
                    return False, None
                frame = np.frombuffer(frame_bytes, dtype=np.uint8)
                try:
                    frame = frame.reshape(
                        (self._ffmpeg_size[1], self._ffmpeg_size[0], 3)
                    )
                except Exception:
                    return False, None
                return True, frame
            except Exception as e:
                logger.exception("ffmpeg read error: %s", e)
                return False, None

        # OpenCV 模式
        if not self.cap or not self.cap.isOpened():
            logger.warning("Capture not opened, retrying in 5s")
            time.sleep(5)
            self._open_capture()
            return False, None

        ret, frame = self.cap.read()
        return ret, frame

    def _open_capture(self):
        """打开/重连采集源"""
        self._stop_ffmpeg_proc()

        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass

        # ffmpeg 子进程模式
        if getattr(settings, "USE_FFMPEG_DECODE", False):
            try:
                self._start_ffmpeg_proc()
                self.status_updated.emit("已连接 (FFmpeg)")
                return
            except Exception:
                logger.exception("Failed to start ffmpeg proc, fallback to OpenCV")

        # OpenCV TCP 传输
        try:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        except Exception:
            pass

        backends = [(cv2.CAP_FFMPEG, "CAP_FFMPEG"), (cv2.CAP_ANY, "CAP_ANY")]
        opened = False
        for flag, name in backends:
            try:
                logger.info("Opening capture backend=%s", name)
                self.cap = cv2.VideoCapture(self.rtsp_url, flag)
            except Exception:
                self.cap = cv2.VideoCapture(self.rtsp_url)
            try:
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            time.sleep(0.2)
            if self.cap is not None and self.cap.isOpened():
                self._backend = name
                opened = True
                break

        if not opened:
            logger.warning("Falling back to default backend")
            self.cap = cv2.VideoCapture(self.rtsp_url)
            try:
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            self._backend = "UNKNOWN"

        if self.cap is not None and self.cap.isOpened():
            self.status_updated.emit(f"已连接 ({self._backend})")
            try:
                w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0.0)
                logger.info(
                    "Capture opened: backend=%s %dx%d %.2ffps",
                    self._backend, w, h, fps,
                )
            except Exception:
                pass
        else:
            self.status_updated.emit("断开")

    def _reopen_capture(self):
        """运行中触发重连"""
        self._open_capture()

    def _start_ffmpeg_proc(self):
        """启动 ffmpeg 子进程用于解码"""
        w, h = self._ffmpeg_size
        self._ffmpeg_frame_bytes = w * h * 3
        cmd = ["ffmpeg", "-rtsp_transport", "tcp", "-i", self.rtsp_url]

        # 硬件加速
        if self._decode_mode == "gpu" and getattr(settings, "USE_FFMPEG_HWACCEL", False):
            hwaccel = getattr(settings, "FFMPEG_HWACCEL", "")
            if hwaccel:
                cmd.extend(["-hwaccel", hwaccel])
                hwaccel_device = getattr(settings, "FFMPEG_HWACCEL_DEVICE", "")
                if hwaccel_device:
                    cmd.extend(["-hwaccel_device", hwaccel_device])
                logger.info("Using ffmpeg HW accel: %s", hwaccel)

        cmd.extend([
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-vf", f"scale={w}:{h}",
            "-nostdin", "-an", "-sn", "-loglevel", "error", "-",
        ])

        logger.info("Starting ffmpeg: %s", " ".join(cmd))

        # stderr 日志
        try:
            log_dir = getattr(settings, "LOG_DIR", Path("logs"))
            stderr_path = Path(log_dir) / f"ffmpeg_{int(time.time())}.log"
            stderr_log = open(stderr_path, "ab")
        except Exception:
            stderr_log = None

        try:
            self.ffmpeg_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            self._backend = "FFMPEG_PROC"
        except FileNotFoundError:
            logger.exception("ffmpeg binary not found")
            if stderr_log:
                stderr_log.close()
            raise
        except Exception:
            logger.exception("Failed to start ffmpeg")
            if stderr_log:
                stderr_log.close()
            raise

        # 后台线程读取 stderr
        def _read_stderr(proc, fp):
            try:
                while True:
                    line = proc.stderr.readline()
                    if not line:
                        break
                    try:
                        txt = line.decode(errors="ignore").strip()
                        logger.debug("ffmpeg: %s", txt)
                        if fp:
                            fp.write(line)
                            fp.flush()
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                try:
                    if fp:
                        fp.close()
                except Exception:
                    pass

        import threading
        threading.Thread(
            target=_read_stderr, args=(self.ffmpeg_proc, stderr_log), daemon=True
        ).start()

        logger.info("ffmpeg pid=%s", getattr(self.ffmpeg_proc, "pid", "N/A"))

    def _stop_ffmpeg_proc(self):
        """停止 ffmpeg 子进程"""
        if self.ffmpeg_proc:
            try:
                self.ffmpeg_proc.kill()
            except Exception:
                pass
            try:
                self.ffmpeg_proc.stdout.close()
            except Exception:
                pass
            try:
                self.ffmpeg_proc.stderr.close()
            except Exception:
                pass
            self.ffmpeg_proc = None
            logger.info("ffmpeg proc stopped")

    def _release_resources(self):
        """释放所有资源"""
        self._stop_ffmpeg_proc()
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        # 清空帧队列
        try:
            while not self._frame_queue.empty():
                self._frame_queue.get_nowait()
        except Exception:
            pass
        logger.info("PreviewThread resources released")

    # ── 属性 ──────────────────────────────────────────

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def current_fps(self) -> float:
        return self._current_fps
