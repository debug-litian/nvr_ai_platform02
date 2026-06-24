import threading
import time
import queue
import cv2
import os
from pathlib import Path
from typing import Optional
from config import settings
from utils.logger import get_logger
from utils import video_player

logger = get_logger("stream_capture")


class StreamCapture:
    def __init__(self, rtsp_url: Optional[str] = None, max_queue=64):
        self.rtsp_url = rtsp_url or settings.RTSP_URL
        self.cap = None
        self.thread = None
        self.stopped = threading.Event()
        self.q = queue.Queue(maxsize=max_queue)
        self._backend = None
        self._last_frame_time = 0
        self._last_success_time = 0
        self._ffmpeg_preferred = True

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
        # Force OpenCV to use TCP transport for RTSP
        try:
            os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'
        except Exception:
            pass

        # Try preferred backend first (FFMPEG)
        backends = []
        if self._ffmpeg_preferred:
            backends = [(cv2.CAP_FFMPEG, 'CAP_FFMPEG'), (cv2.CAP_ANY, 'CAP_ANY')]
        else:
            backends = [(cv2.CAP_ANY, 'CAP_ANY'), (cv2.CAP_FFMPEG, 'CAP_FFMPEG')]

        opened = False
        for flag, name in backends:
            try:
                logger.info("Opening capture using backend %s", name)
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
            logger.warning("Failed to open capture with preferred backends, attempting default open")
            self.cap = cv2.VideoCapture(self.rtsp_url)
            try:
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            self._backend = 'UNKNOWN'

        # Log capture properties if opened
        if self.cap is not None and self.cap.isOpened():
            try:
                w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0.0)
                count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                fourcc = int(self.cap.get(cv2.CAP_PROP_FOURCC) or 0)
                fourcc_s = ''.join([chr((fourcc >> 8*i) & 0xFF) for i in range(4)]) if fourcc != 0 else ''
                logger.info("Capture opened: backend=%s width=%d height=%d fps=%.2f frames=%d fourcc=%s",
                            self._backend, w, h, fps, count, fourcc_s)
            except Exception:
                logger.exception("Failed to read capture properties")

    def _run(self):
        try:
            self._open_capture()
            consecutive_bad = 0
            last_read_time = time.time()
            last_backend_switch = time.time()
            while not self.stopped.is_set():
                if not self.cap or not self.cap.isOpened():
                    logger.warning("Capture not opened, retrying in 5s")
                    time.sleep(5)
                    self._open_capture()
                    continue

                ret, frame = self.cap.read()
                now = time.time()
                if not ret or frame is None:
                    # detailed debug info
                    logger.debug("cap.read() returned no frame (ret=%s). backend=%s", ret, self._backend)
                    # if no frame read for a while, attempt reopen or switch backend
                    if now - last_read_time > 5.0:
                        logger.warning("No frames read for %.1fs, reopening capture", now - last_read_time)
                        self._open_capture()
                        last_read_time = now
                        # if using FFMPEG and still no frames for 10s, switch backend
                    if self._backend == 'CAP_FFMPEG' and (now - last_backend_switch) > 10.0:
                        logger.info("Switching backend to CAP_ANY due to no frames")
                        self._ffmpeg_preferred = False
                        last_backend_switch = now
                        self._open_capture()
                    # if 30s no frames, fallback to ffplay
                    if now - self._last_success_time > 30.0 and self._last_success_time != 0:
                        logger.error("No frames for 30s, falling back to ffplay for manual viewing")
                        try:
                            video_player.play_video_at(self.rtsp_url, 0)
                        except Exception:
                            logger.exception("Failed to spawn ffplay fallback")
                    time.sleep(0.05)
                    continue

                # got a frame
                self._last_success_time = now
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
