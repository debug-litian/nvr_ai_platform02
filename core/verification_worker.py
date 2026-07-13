"""
verification_worker.py — FTP 报警核验工作线程

从 MainWindow 接收待核验的 FTP 报警记录，在线程中调用 AlarmVerifier
执行 YOLO 检测 + 配置校验，完成后通过信号返回结果。

设计：
- QThread：避免阻塞 UI
- 内部 queue.Queue：线程安全地接收任务
- 共享 YoloDetector：不重复加载模型

信号：
- verification_complete(dict)：单条核验完成
- worker_status(str)：状态更新
"""

import queue
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from core.alarm_verifier import AlarmVerifier, VerificationResult
from utils.logger import get_logger

logger = get_logger("verification_worker")


class VerificationWorker(QThread):
    """FTP 报警文件核验工作线程"""

    verification_complete = pyqtSignal(dict)   # VerificationResult.to_dict()
    verification_error = pyqtSignal(str, str)   # (filepath, error_msg)
    worker_status = pyqtSignal(str)             # 状态更新

    def __init__(
        self,
        detector=None,
        profile_path: Optional[str] = None,
        parent=None,
    ):
        """
        参数:
            detector: YoloDetector 实例（由 MainWindow 传入，共享使用）
            profile_path: nvr_profile.json 路径
        """
        super().__init__(parent)
        self._queue: queue.Queue = queue.Queue()
        self._stopped = False
        self._verifier: Optional[AlarmVerifier] = None
        self._detector = detector
        self._profile_path = profile_path

    # ── 公共接口 ──────────────────────────────────────

    def set_detector(self, detector):
        """设置 YOLO 检测器"""
        self._detector = detector
        if self._verifier:
            self._verifier.set_detector(detector)

    def set_profile_path(self, path: str):
        """设置 NVR 配置文件路径"""
        self._profile_path = path

    def enqueue(self, record: dict):
        """
        线程安全地添加一条待核验记录。

        参数:
            record: parse_filename() 返回的字典
        """
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            logger.warning("核验队列已满，丢弃记录: %s", record.get("original", "?"))

    def stop(self):
        """优雅停止：等待当前任务完成，然后退出"""
        self._stopped = True

    # ── QThread 生命周期 ──────────────────────────────

    def run(self):
        """QThread 主循环"""
        self._stopped = False

        # 初始化 AlarmVerifier
        self._verifier = AlarmVerifier(
            detector=self._detector,
            profile_path=self._profile_path,
        )

        self.worker_status.emit("核验引擎就绪")
        logger.info("VerificationWorker 已启动")

        try:
            while not self._stopped:
                try:
                    record = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                if record is None:
                    continue

                filename = record.get("original", "?")
                self.worker_status.emit(f"核验中: {filename}")

                try:
                    result = self._verifier.verify(record)
                    self.verification_complete.emit(result.to_dict())
                except Exception as e:
                    err_msg = f"核验失败: {e}"
                    logger.exception("核验异常: %s", filename)
                    self.verification_error.emit(
                        record.get("full_path", ""), str(e)
                    )

        except Exception as e:
            logger.exception("VerificationWorker 致命异常: %s", e)
        finally:
            self.worker_status.emit("核验引擎空闲")
            logger.info("VerificationWorker 已退出")

        # 处理完成后继续消费队列
        while not self._stopped:
            try:
                record = self._queue.get_nowait()
                if record:
                    try:
                        result = self._verifier.verify(record)
                        self.verification_complete.emit(result.to_dict())
                    except Exception as e:
                        self.verification_error.emit(
                            record.get("full_path", ""), str(e)
                        )
            except queue.Empty:
                break

    @property
    def pending_count(self) -> int:
        """当前队列中待处理的记录数"""
        return self._queue.qsize()
