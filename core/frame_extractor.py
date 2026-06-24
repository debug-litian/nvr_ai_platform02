import time
import threading
from typing import Callable, Optional
from config import settings
from utils.logger import get_logger

logger = get_logger("frame_extractor")


class FrameExtractor:
    def __init__(self, source, sample_fps: Optional[float] = None, callback: Optional[Callable] = None):
        self.source = source
        self.sample_fps = sample_fps or settings.SAMPLE_FPS
        self.callback = callback
        self.thread = None
        self.stopped = threading.Event()
        self.running = False

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopped.clear()
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("FrameExtractor started")

    def stop(self):
        self.running = False
        self.stopped.set()
        if self.thread:
            # allow more time for graceful shutdown
            self.thread.join(timeout=5)
        logger.info("FrameExtractor stopped (线程已退出)")

    def _run(self):
        interval = 1.0 / max(1.0, float(self.sample_fps))
        last = 0
        while self.running:
            item = self.source.read(timeout=0.5)
            if item is None:
                continue
            ts, frame = item
            if ts - last >= interval:
                last = ts
                try:
                    if self.callback:
                        self.callback(ts, frame)
                except Exception as e:
                    logger.exception("Extractor callback error: %s", e)
        logger.info("FrameExtractor loop exit")
