"""
ftp_filename_parser.py — Reolink NVR FTP 文件名解析器

解析 Reolink NVR FTP 上传的文件名，格式：

    NVR-REOCYP_{通道号}_{YYYYMMDDHHMMSS}.{扩展名}

示例:
    NVR-REOCYP_01_20260713000614.jpg  → channel=1,  2026-07-13 00:06:14, .jpg
    NVR-REOCYP_14_20260713000642.mp4  → channel=14, 2026-07-13 00:06:42, .mp4

报警类型由 FTP 子目录名推断（如 human/, vehicle/, pet/, motion/）。

纯函数设计，无副作用，方便单元测试。
"""

import re
import os
from datetime import datetime
from typing import Optional, Dict

# Reolink 文件名正则: NVR名称_通道号_14位时间戳.扩展名
_REOLINK_PATTERN = re.compile(
    r"^(.+)_(\d{1,2})_(\d{14})\.(jpg|jpeg|png|mp4|avi|mkv)$",
    re.IGNORECASE,
)

# 支持的图片/视频扩展名
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
VIDEO_EXTS = {".mp4", ".avi", ".mkv"}

# 日期目录模式（如 2026-07-13），不是报警类型目录
_DATE_DIR_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_filename(filepath: str) -> Optional[Dict]:
    """
    解析 Reolink FTP 文件名，返回结构化数据。

    参数:
        filepath: 文件的完整路径，如 D:\\FTP_Upload\\human\\NVR-REOCYP_01_20260713000614.jpg

    返回:
        None — 文件名格式不匹配
        dict:
        {
            "channel": int,            # 通道号 (0-based，和文件名一致)
            "timestamp": datetime,     # 时间戳
            "alarm_type": str,         # 报警类型标识符 "human"/"vehicle"/"pet"/"motion"
            "extension": str,          # 扩展名 ".jpg" / ".mp4"
            "file_type": str,          # "image" / "video"
            "original": str,           # 原始文件名
            "full_path": str,          # 完整路径
            "nvr_name": str,           # NVR 设备名
        }

    示例:
        >>> parse_filename("D:\\\\FTP_Upload\\\\human\\\\NVR-REOCYP_01_20260713000614.jpg")
        {"channel": 1, "timestamp": datetime(2026,7,13,0,6,14), "alarm_type": "human", ...}
    """
    filename = os.path.basename(filepath)
    dirpath = os.path.dirname(filepath)

    # ── 1. 正则匹配文件名 ──────────────────────────
    match = _REOLINK_PATTERN.match(filename)
    if not match:
        return None

    nvr_name = match.group(1)
    channel_str = match.group(2)
    ts_str = match.group(3)
    ext = "." + match.group(4).lower()

    # ── 2. 解析通道号 ──────────────────────────────
    try:
        channel = int(channel_str)
    except ValueError:
        return None

    # ── 3. 解析时间戳 YYYYMMDDHHMMSS ───────────────
    try:
        ts = datetime.strptime(ts_str, "%Y%m%d%H%M%S")
    except ValueError:
        return None

    # ── 4. 从父目录名推断报警类型 ──────────────────
    # 策略：逐级向上查找目录链，匹配已知的报警类型目录名
    # 支撑 Reolink "按日期生成子目录" 的目录结构：
    #   D:\FTP_Upload\person\2026-07-13\xxx.jpg
    #   D:\FTP_Upload\human\xxx.jpg
    from core.alarm_types import get_alarm_type_by_dir

    alarm_type = "unknown"
    parent_dir = os.path.basename(dirpath)

    # 先检查当前父目录
    at = get_alarm_type_by_dir(parent_dir)
    if at is not None:
        alarm_type = at
    else:
        # 向上查找：检查父目录的父目录是否匹配（日期目录场景）
        grandparent = os.path.basename(os.path.dirname(dirpath))
        at = get_alarm_type_by_dir(grandparent)
        if at is not None:
            alarm_type = at
        else:
            # 如果都不匹配，用当前目录名（可能是日期目录等）
            alarm_type = parent_dir if parent_dir else "unknown"

    # ── 5. 文件类型 ────────────────────────────────
    if ext in IMAGE_EXTS:
        file_type = "image"
    elif ext in VIDEO_EXTS:
        file_type = "video"
    elif ext == ".txt":
        # NVR 有时会上传 .txt 日志文件，跳过
        return None
    else:
        file_type = "unknown"

    return {
        "channel": channel,
        "timestamp": ts,
        "alarm_type": alarm_type,
        "extension": ext,
        "file_type": file_type,
        "original": filename,
        "full_path": filepath,
        "nvr_name": nvr_name,
    }


def is_valid_reolink_filename(filename: str) -> bool:
    """快速检查文件名是否为有效的 Reolink 格式"""
    return _REOLINK_PATTERN.match(os.path.basename(filename)) is not None
