import threading
import time
import queue
import cv2
import os
import subprocess
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
        self.running = False
        self.q = queue.Queue(maxsize=max_queue)
        self._backend = None
        self._last_frame_time = 0
        self._last_success_time = 0
        self._ffmpeg_preferred = True
        # ffmpeg subprocess decoding
        self.ffmpeg_proc = None
        self._ffmpeg_frame_bytes = 0
        self._ffmpeg_size = tuple(settings.FFMPEG_DECODE_SIZE) if hasattr(settings, 'FFMPEG_DECODE_SIZE') else (640, 480)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopped.clear()
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("StreamCapture started")

    def stop(self):
        # signal thread to stop
        self.running = False
        self.stopped.set()
        # clear queue to release consumers
        try:
            with self.q.mutex:
                self.q.queue.clear()
        except Exception:
            pass
        if self.thread:
            # wait longer for thread to exit gracefully
            self.thread.join(timeout=10)
        # ensure capture released
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
        # stop ffmpeg proc if running
        try:
            self._stop_ffmpeg_proc()
        except Exception:
            pass
        logger.info("StreamCapture stopped")

    def _open_capture(self):
        # stop any running ffmpeg proc first
        self._stop_ffmpeg_proc()

        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
        # If configured, use external ffmpeg process for decoding
        if getattr(settings, 'USE_FFMPEG_DECODE', False):
            try:
                self._start_ffmpeg_proc()
                return
            except Exception:
                logger.exception("Failed to start ffmpeg proc, falling back to OpenCV capture")
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

    def _start_ffmpeg_proc(self):
        # start ffmpeg to output raw bgr24 frames
        w, h = self._ffmpeg_size
        self._ffmpeg_frame_bytes = w * h * 3
        cmd = ['ffmpeg', '-rtsp_transport', 'tcp', '-i', self.rtsp_url]
        if getattr(settings, 'USE_FFMPEG_HWACCEL', False) and getattr(settings, 'FFMPEG_HWACCEL', ''):
            hwaccel = settings.FFMPEG_HWACCEL
            cmd.extend(['-hwaccel', hwaccel])
            if getattr(settings, 'FFMPEG_HWACCEL_DEVICE', ''):
                cmd.extend(['-hwaccel_device', settings.FFMPEG_HWACCEL_DEVICE])
            logger.info("Using ffmpeg hardware accel: %s", hwaccel)
        cmd.extend([
            '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-vf', f'scale={w}:{h}',
            '-nostdin', '-an', '-sn', '-loglevel', 'error', '-'
        ])
        logger.info("Starting ffmpeg subprocess for decode: %s", ' '.join(cmd))
        try:
            # open stderr log file under settings.LOG_DIR if available
            stderr_log = None
            try:
                log_dir = getattr(__import__('config.settings', fromlist=['LOG_DIR']), 'LOG_DIR')
                fname = f"ffmpeg_{int(time.time())}.log"
                stderr_path = Path(log_dir) / fname
                stderr_log = open(stderr_path, 'ab')
                logger.info("ffmpeg stderr will be saved to %s", str(stderr_path))
            except Exception:
                stderr_log = None

            self.ffmpeg_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self._backend = 'FFMPEG_PROC'
            logger.info("ffmpeg pid=%s", getattr(self.ffmpeg_proc, 'pid', 'N/A'))

            # spawn stderr reader thread that logs and writes to file
            def _read_err(proc, stderr_fp):
                try:
                    while True:
                        line = proc.stderr.readline()
                        if not line:
                            break
                        try:
                            txt = line.decode(errors='ignore').strip()
                            logger.debug("ffmpeg: %s", txt)
                            if stderr_fp:
                                try:
                                    stderr_fp.write(line)
                                    stderr_fp.flush()
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except Exception:
                    pass
                finally:
                    try:
                        if stderr_fp:
                            stderr_fp.close()
                    except Exception:
                        pass

            threading.Thread(target=_read_err, args=(self.ffmpeg_proc, stderr_log), daemon=True).start()
        except FileNotFoundError:
            logger.exception("ffmpeg binary not found in PATH")
            raise
        except Exception:
            logger.exception("Failed to start ffmpeg subprocess")
            # ensure file closed
            try:
                if stderr_log:
                    stderr_log.close()
            except Exception:
                pass
            raise

    def _stop_ffmpeg_proc(self):
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

    def _run(self):
        try:
            self._open_capture()
            consecutive_bad = 0
            last_read_time = time.time()
            last_backend_switch = time.time()
            while not self.stopped.is_set():
                # if using ffmpeg subprocess decoding
                if getattr(settings, 'USE_FFMPEG_DECODE', False) and self.ffmpeg_proc is not None:
                    try:
                        frame_bytes = self.ffmpeg_proc.stdout.read(self._ffmpeg_frame_bytes)
                        if not frame_bytes or len(frame_bytes) < self._ffmpeg_frame_bytes:
                            logger.debug("ffmpeg stdout read incomplete frame (len=%s)", len(frame_bytes) if frame_bytes else 0)
                            # restart ffmpeg proc
                            time.sleep(0.5)
                            self._start_ffmpeg_proc()
                            continue
                        import numpy as _np
                        frame = _np.frombuffer(frame_bytes, dtype=_np.uint8)
                        try:
                            frame = frame.reshape((self._ffmpeg_size[1], self._ffmpeg_size[0], 3))
                        except Exception:
                            logger.warning("ffmpeg frame reshape failed")
                            continue
                        ret = True
                    except Exception as e:
                        logger.exception("Error reading ffmpeg stdout: %s", e)
                        ret = False
                        frame = None
                else:
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

                # got a frame - validate
                self._last_success_time = now
                last_read_time = now
                try:
                    import numpy as _np
                    if not isinstance(frame, _np.ndarray):
                        logger.warning("Read frame is not ndarray, skipping: %s", type(frame))
                        continue
                    if frame.size == 0:
                        logger.warning("Read empty frame (size=0), skipping")
                        continue
                    # if frame has unexpected number of channels, try to handle
                    if frame.ndim == 3 and frame.shape[2] not in (3, 4):
                        logger.warning("Read frame with unusual channels=%s", frame.shape[2])
                except Exception:
                    pass

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
            logger.info("StreamCapture thread 线程已退出")

    def read(self, timeout=0.1):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None
