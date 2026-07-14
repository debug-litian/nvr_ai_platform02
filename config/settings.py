import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# RTSP default (updated)
RTSP_URL = "rtsp://admin:111111..@192.168.124.2:554/Preview_10_main"

DEVICE = os.environ.get("NVR_DEVICE", "cpu")
SAMPLE_FPS = 2
TOP_K = 10
# UI 刷新帧率（Hz），主线程 QTimer 按此频率从 PreviewThread 取帧显示
UI_REFRESH_FPS = 15
# PreviewThread 拉流帧率上限，避免信号队列堆积
MAX_CAPTURE_FPS = 30
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
USE_FFMPEG_DECODE = True
# 当使用 ffmpeg 解码时，输出帧尺寸（width, height）
FFMPEG_DECODE_SIZE = (640, 480)
# 是否尝试使用 ffmpeg 硬件加速（例如 cuda/qsv/d3d11va），需根据本机 ffmpeg 构建调整
USE_FFMPEG_HWACCEL = False
FFMPEG_HWACCEL = ""
FFMPEG_HWACCEL_DEVICE = ""

# 实时索引缓存最大帧数
REALTIME_INDEX_MAX_ITEMS = 256
# 视频索引抽帧率（每秒采样帧数）
VIDEO_INDEX_SAMPLE_FPS = 1.0

YOLO_CONFIDENCE_THRESHOLD = 0.25

# 是否在预览画面上绘制 YOLO 检测框（调试用开关）
SHOW_YOLO_BOXES = True

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

# 日志目录（用于保存 ffmpeg stderr 等运行时日志）
LOG_DIR = ROOT / "logs"
os.makedirs(LOG_DIR, exist_ok=True)

# ═══════════════════════════════════════════════════════════
# FTP 报警核验配置
# ═══════════════════════════════════════════════════════════

# FTP 上传目录（Reolink NVR FTP 文件存放路径）
FTP_UPLOAD_DIR = os.environ.get("NVR_FTP_DIR", r"D:\FTP_Upload")

# FTP 子目录名 → 报警类型（和 Reolink NVR 的 FTP 报警类型子目录一致）
FTP_SUBDIRS = ["human", "vehicle", "pet", "motion"]

# 新文件到达后等待此秒数再处理（确保 FTP 传输完毕）
FTP_PROCESSING_DELAY_SEC = 2.0

# 视频报警文件采样帧率（每秒抽多少帧跑 YOLO）
ALARM_VIDEO_SAMPLE_FPS = 1.0

# NVR 预期配置文件路径
NVR_PROFILE_PATH = ROOT / "config" / "nvr_profile.json"

# NVR 总通道数（用于计算通道覆盖率）
NVR_TOTAL_CHANNELS = 16

# 测试报告导出目录
REPORT_EXPORT_DIR = ROOT / "reports"
os.makedirs(REPORT_EXPORT_DIR, exist_ok=True)

# ═══════════════════════════════════════════════════════════
# reolink_aio API 配置
# ═══════════════════════════════════════════════════════════

# NVR HTTP API 连接参数（reolink_aio SDK）
NVR_HOST = os.environ.get("NVR_HOST", "192.168.124.2")
NVR_USERNAME = os.environ.get("NVR_USERNAME", "admin")
NVR_PASSWORD = os.environ.get("NVR_PASSWORD", "")

# 是否启用 reolink_aio SDK（需 Python 3.11+）
USE_REOLINK_API = os.environ.get("NVR_USE_API", "1") == "1"

def get_device():
    # Normalize device string
    d = DEVICE
    if d.lower() in ("cpu", "none", ""):
        return "cpu"
    return d
