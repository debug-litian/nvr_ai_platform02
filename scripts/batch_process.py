import os
from core.feature_extractor import CLIPFeatureExtractor
from core.index_builder import IndexBuilder
from utils.file_utils import save_frame_image
from utils.logger import get_logger
from config import settings
import cv2

logger = get_logger("batch_process")

def process_video(video_path):
    cap = cv2.VideoCapture(str(video_path))
    fe = CLIPFeatureExtractor()
    ib = IndexBuilder()
    frames = []
    metas = []
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if idx % int(30 // settings.SAMPLE_FPS + 1) == 0:
            try:
                from PIL import Image
                img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                vec = fe.encode_image(img)
                frames.append(vec)
                metas.append({"video": str(video_path), "ts": idx})
            except Exception:
                logger.exception("feature extract fail")
        idx += 1
    if frames:
        ib.add(frames, metas)
        ib.save()

if __name__ == '__main__':
    import sys
    for p in sys.argv[1:]:
        process_video(p)
