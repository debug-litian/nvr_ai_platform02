"""
ftp_monitor.py — FTP 目录监控线程

使用 watchdog 库监听 FTP 上传目录及其子目录，检测 Reolink NVR
上传的新文件（.jpg / .mp4），解析文件名后通过 PyQt5 信号通知主线程。

架构：
- FTPMonitor(QThread)：主控制线程，管理 watchdog Observer
- ReolinkFileHandler(FileSystemEventHandler)：watchdog 事件处理器

信号：
- file_detected(dict)：新文件到达并解析完成
- monitor_error(str)：监控错误
- monitor_status(str)：状态更新
"""

import os
import time
import threading
import queue
from datetime import datetime
from typing import Optional, List, Callable, Dict

from PyQt5.QtCore import QThread, pyqtSignal

from config import settings
from core.ftp_filename_parser import parse_filename
from utils.logger import get_logger

logger = get_logger("ftp_monitor")

# 尝试导入 watchdog
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    Observer = None
    FileSystemEventHandler = object  # 避免类定义时报错
    logger.warning("watchdog 未安装，FTP 监控不可用。请执行: pip install watchdog")


class ReolinkFileHandler(FileSystemEventHandler):
    """
    watchdog 文件事件处理器。

    - 新文件创建后等待 delay 秒（让 FTP 写入完成）
    - 过滤：只处理 *.jpg, *.mp4（在 __init__ 的 patterns 中配置）
    - 回调通知 FTPMonitor
    """

    def __init__(
        self,
        callback: Callable[[Dict], None],
        patterns: Optional[List[str]] = None,
        delay: float = 2.0,
    ):
        super().__init__()
        self._callback = callback
        self._patterns = patterns or settings.FTP_SUBDIRS.copy()
        self._delay = delay
        self._pending: Dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def on_created(self, event):
        """文件/目录创建事件"""
        if event.is_directory:
            return

        filepath = event.src_path
        filename = os.path.basename(filepath)
        ext = os.path.splitext(filename)[1].lower()

        # 只监控图片和视频文件
        if ext not in (".jpg", ".jpeg", ".png", ".mp4", ".avi", ".mkv"):
            return

        logger.info("检测到新文件: %s", filepath)

        # 取消该文件的旧定时器（如果存在），重新计时
        with self._lock:
            if filepath in self._pending:
                self._pending[filepath].cancel()

            timer = threading.Timer(
                self._delay,
                self._on_file_ready,
                args=[filepath],
            )
            self._pending[filepath] = timer
            timer.start()

    def _on_file_ready(self, filepath: str):
        """延迟后文件写入完成，触发回调"""
        with self._lock:
            self._pending.pop(filepath, None)

        # 检查文件是否还存在且非空
        if not os.path.exists(filepath):
            logger.warning("文件已不存在: %s", filepath)
            return

        try:
            size = os.path.getsize(filepath)
            if size == 0:
                logger.warning("文件大小为 0，跳过: %s", filepath)
                return
        except OSError:
            return

        # 解析文件名
        result = parse_filename(filepath)
        if result is None:
            # 尝试解析不含报警类型子目录的情况
            result = parse_filename(filepath)
            if result is None:
                logger.warning("无法解析文件名: %s", filepath)
                return

        logger.info(
            "文件就绪: ch=%d type=%s ts=%s ext=%s",
            result["channel"],
            result.get("alarm_type", "?"),
            result["timestamp"].strftime("%H:%M:%S"),
            result["extension"],
        )

        self._callback(result)

    def cleanup(self):
        """取消所有待处理定时器"""
        with self._lock:
            for timer in self._pending.values():
                timer.cancel()
            self._pending.clear()


class FTPMonitor(QThread):
    """
    FTP 文件监控线程。

    用法:
        monitor = FTPMonitor(watch_dir=r"D:\\FTP_Upload")
        monitor.file_detected.connect(on_new_file)
        monitor.start()
        ...
        monitor.stop()
    """

    file_detected = pyqtSignal(dict)      # 新文件解析结果
    monitor_error = pyqtSignal(str)       # 错误消息
    monitor_status = pyqtSignal(str)      # 状态更新 ("running" / "stopped" / "error")

    def __init__(
        self,
        watch_dir: Optional[str] = None,
        patterns: Optional[List[str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._watch_dir = watch_dir or settings.FTP_UPLOAD_DIR
        self._patterns = patterns or ["*.jpg", "*.mp4"]
        self._stopped = False
        self._observer: Optional["Observer"] = None
        self._handler: Optional[ReolinkFileHandler] = None

    # ── 公共接口 ──────────────────────────────────────

    def set_watch_dir(self, path: str):
        """更换监控目录（需在 stopped 状态下调用）"""
        self._watch_dir = path

    def stop(self):
        """优雅停止监控"""
        self._stopped = True
        logger.info("FTPMonitor 停止请求")

    # ── QThread 生命周期 ──────────────────────────────

    def run(self):
        if not HAS_WATCHDOG:
            err = "watchdog 库未安装，无法启动 FTP 监控。请执行: pip install watchdog"
            logger.error(err)
            self.monitor_error.emit(err)
            return

        if not os.path.isdir(self._watch_dir):
            err = f"FTP 监控目录不存在: {self._watch_dir}"
            logger.error(err)
            self.monitor_error.emit(err)
            return

        self._stopped = False

        try:
            self._observer = Observer()
            self._handler = ReolinkFileHandler(
                callback=self._on_file_detected,
                patterns=self._patterns,
                delay=settings.FTP_PROCESSING_DELAY_SEC,
            )

            # 递归监控整个 FTP 目录树
            self._observer.schedule(self._handler, self._watch_dir, recursive=True)
            self._observer.start()

            self.monitor_status.emit("FTP 监控已启动")
            logger.info("FTPMonitor 已启动，监控目录: %s", self._watch_dir)

            # 主循环：每秒检查一次是否需要停止
            while not self._stopped:
                time.sleep(1.0)

        except Exception as e:
            err_msg = f"FTP 监控异常: {e}"
            logger.exception(err_msg)
            self.monitor_error.emit(err_msg)

        finally:
            self._cleanup()

    # ── 内部方法 ──────────────────────────────────────

    def _on_file_detected(self, result: Dict):
        """watchdog handler 回调 → 发射 Qt 信号"""
        if self._stopped:
            return
        self.file_detected.emit(result)

    def _cleanup(self):
        """释放 watchdog 资源"""
        if self._handler:
            self._handler.cleanup()
        if self._observer and self._observer.is_alive():
            self._observer.stop()
            try:
                self._observer.join(timeout=5)
            except Exception:
                pass
        self._observer = None
        self._handler = None
        self.monitor_status.emit("FTP 监控已停止")
        logger.info("FTPMonitor 资源已释放")
