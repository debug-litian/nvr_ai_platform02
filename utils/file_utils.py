from pathlib import Path
from PIL import Image

def ensure_dir(p: Path):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def save_frame_image(path: Path, frame):
    p = ensure_dir(path)
    # frame is BGR (opencv) -> convert to RGB
    try:
        import cv2
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        img.save(str(p))
    except Exception:
        # fallback: try Pillow if already RGB
        Image.fromarray(frame).save(str(p))
