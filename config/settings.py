import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# RTSP default (empty placeholder)
RTSP_URL = "rtsp://admin:password@127.0.0.1:554/Preview_01_main"

DEVICE = os.environ.get("NVR_DEVICE", "cpu")
SAMPLE_FPS = 2
TOP_K = 10
GREEN_LINE_THRESHOLD = 0.30
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
