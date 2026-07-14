import subprocess
import shutil
import os
from utils.logger import get_logger

logger = get_logger("video_player")


def find_ffplay():
    """跨平台查找 ffplay 可执行文件"""
    # 优先 PATH
    p = shutil.which("ffplay") or shutil.which("ffplay.exe")
    if p:
        return p

    # Windows 常见安装路径
    if platform.system() == "Windows":
        import os as _os
        for base in [
            _os.environ.get("ProgramFiles", r"C:\Program Files"),
            _os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ]:
            candidate = _os.path.join(base, "ffmpeg", "bin", "ffplay.exe")
            if _os.path.exists(candidate):
                return candidate

    # Linux 常见路径
    for p in ["/usr/bin/ffplay", "/usr/local/bin/ffplay"]:
        if os.path.exists(p):
            return p

    # macOS Homebrew
    if os.path.exists("/opt/homebrew/bin/ffplay"):
        return "/opt/homebrew/bin/ffplay"

    return None


def _open_with_default(video_path: str):
    """跨平台用系统默认程序打开文件"""
    import platform
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(video_path)
        elif system == "Darwin":  # macOS
            subprocess.Popen(["open", video_path])
        else:  # Linux / other
            subprocess.Popen(["xdg-open", video_path])
        return True
    except Exception:
        logger.exception("Failed to open video with default player")
        return False


def play_video_at(video_path: str, seconds: float = 0.0, duration: float = None):
    ffplay = find_ffplay()
    if ffplay:
        cmd = [ffplay, "-ss", str(seconds), "-autoexit"]
        if duration is not None and duration > 0:
            cmd.extend(["-t", str(duration)])
        cmd.append(video_path)
        try:
            subprocess.Popen(cmd)
            return True
        except Exception:
            logger.exception("Failed to launch ffplay")
    # fallback: open with system default. Note: cannot seek.
    return _open_with_default(video_path)
