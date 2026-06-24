import threading
import time
import queue
import cv2
from pathlib import Path
from typing import Optional
from config import settings
from utils.logger import get_logger

logger = get_logger("stream_capture")


class StreamCapture:
    def __init__(self, rtsp_url: Optional[str] = None, max_queue=64):
        self.rtsp_url = rtsp_url or settings.RTSP_URL
        self.cap = None
        self.thread = None
        self.stopped = threading.Event()
        self.q = queue.Queue(maxsize=max_queue)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopped.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("StreamCapture started")

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=2)
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
        logger.info("StreamCapture stopped")

    def _open_capture(self):
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
        # open capture with url
        self.cap = cv2.VideoCapture(self.rtsp_url)
        try:
            # reduce internal buffer where supported
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

    def _run(self):
        try:
            self._open_capture()
            consecutive_bad = 0
            last_read_time = time.time()
            while not self.stopped.is_set():
                if not self.cap or not self.cap.isOpened():
                    logger.warning("Capture not opened, retrying in 1s")
                    time.sleep(1)
                    self._open_capture()
                    continue

                ret, frame = self.cap.read()
                now = time.time()
                if not ret or frame is None:
                    # if no frame read for a while, attempt reopen
                    if now - last_read_time > 2.0:
                        logger.warning("No frames read for %.1fs, reopening capture", now - last_read_time)
                        self._open_capture()
                        last_read_time = now
                    time.sleep(0.05)
                    continue

                last_read_time = now

                # simple bad-frame checks: empty, tiny variance, wrong shape
                try:
                    import numpy as np
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    std = float(np.std(gray))
                    if std < settings.BAD_FRAME_STD_THRESHOLD:
                        consecutive_bad += 1
                        logger.debug("Bad frame detected (std=%.2f), count=%d", std, consecutive_bad)
                        if consecutive_bad >= settings.MAX_CONSECUTIVE_BAD_FRAMES:
                            logger.warning("Too many consecutive bad frames, reopening capture")
                            self._open_capture()
                            consecutive_bad = 0
                        continue
                    else:
                        consecutive_bad = 0
                except Exception:
                    # if validation fails, just continue with frame
                    consecutive_bad = 0

                try:
                    # drop oldest if full
                    if self.q.full():
                        try:
                            _ = self.q.get_nowait()
                        except queue.Empty:
                            pass
                    self.q.put_nowait((time.time(), frame))
                except queue.Full:
                    pass
        except Exception as e:
            logger.exception("StreamCapture error: %s", e)
        finally:
            try:
                if self.cap:
                    self.cap.release()
            except Exception:
                pass

    def read(self, timeout=0.1):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None
