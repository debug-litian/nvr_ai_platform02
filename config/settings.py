import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# RTSP default (updated)
RTSP_URL = "rtsp://admin:111111..@192.168.124.4:554/Preview_01_main"

DEVICE = os.environ.get("NVR_DEVICE", "cpu")
SAMPLE_FPS = 2
TOP_K = 10
GREEN_LINE_THRESHOLD = 0.65
# 连续多少帧超过阈值才触发告警，减少误报
GREEN_LINE_CONSECUTIVE = 3

# 帧质量判定（用于丢弃坏帧）: 灰度图像标准差小于该值视为坏帧
# 帧质量判定（用于丢弃坏帧）: 灰度图像标准差小于该值视为坏帧
# 提高阈值以避免误杀
BAD_FRAME_STD_THRESHOLD = 12.0
# 连续坏帧超过该数量则尝试重连
MAX_CONSECUTIVE_BAD_FRAMES = 8

# 是否使用独立的 ffmpeg 进程做解码（更稳定但需系统安装 ffmpeg）
USE_FFMPEG_DECODE = False
# 当使用 ffmpeg 解码时，输出帧尺寸（width, height）
FFMPEG_DECODE_SIZE = (640, 480)

YOLO_CONFIDENCE_THRESHOLD = 0.5

DATA_DIR = ROOT / "data"
VIDEOS_DIR = DATA_DIR / "videos"
INDICES_DIR = DATA_DIR / "indices"
MAPPINGS_DIR = DATA_DIR / "mappings"
ALERTS_DIR = DATA_DIR / "alerts"

INDEX_FILE = INDICES_DIR / "clip_index.faiss"
MAPPING_FILE = MAPPINGS_DIR / "mapping.json"

MODELS_DIR = ROOT / "models"
CLIP_MODEL_DIR = MODELS_DIR / "clip"
YOLO_MODEL_DIR = MODELS_DIR / "yolov8"

os.makedirs(VIDEOS_DIR, exist_ok=True)
os.makedirs(INDICES_DIR, exist_ok=True)
os.makedirs(MAPPINGS_DIR, exist_ok=True)
os.makedirs(ALERTS_DIR, exist_ok=True)
os.makedirs(CLIP_MODEL_DIR, exist_ok=True)
os.makedirs(YOLO_MODEL_DIR, exist_ok=True)

def get_device():
    # Normalize device string
    d = DEVICE
    if d.lower() in ("cpu", "none", ""):
        return "cpu"
    return d
