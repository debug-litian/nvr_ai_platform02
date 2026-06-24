import subprocess
import shutil
import os
from utils.logger import get_logger

logger = get_logger("video_player")


def find_ffplay():
    # try PATH
    p = shutil.which("ffplay") or shutil.which("ffplay.exe")
    if p:
        return p
    # common Windows locations
    possible = [
        os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "ffmpeg", "bin", "ffplay.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "ffmpeg", "bin", "ffplay.exe"),
    ]
    for pp in possible:
        if os.path.exists(pp):
            return pp
    return None


def play_video_at(video_path: str, seconds: float = 0.0):
    ffplay = find_ffplay()
    if ffplay:
        cmd = [ffplay, "-ss", str(seconds), "-autoexit", "-nodisp", video_path]
        try:
            subprocess.Popen(cmd)
            return True
        except Exception:
            logger.exception("Failed to launch ffplay")
    # fallback: open with system default. Note: cannot seek.
    try:
        if os.name == 'nt':
            os.startfile(video_path)
        else:
            subprocess.Popen(["xdg-open", video_path])
        return True
    except Exception:
        logger.exception("Failed to open video with default player")
    return False
