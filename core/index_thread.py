"""
IndexThread — 离线视频索引构建线程

继承 QThread，每次构建索引时新建一个线程实例。
负责：
- 读取本地 MP4/AVI 等视频文件
- 每隔 N 帧采样（基于 SAMPLE_FPS）
- CLIP 图像编码
- FAISS 向量索引构建
- 保存索引和映射到磁盘

信号：
- progress_updated(int, int): (当前采样帧数, 总预计帧数)
- index_built(str): 索引保存路径（构建成功）
- index_error(str): 错误信息（构建失败）
"""
import json
from pathlib import Path
from typing import Optional

import numpy as np
import cv2
from PyQt5.QtCore import QThread, pyqtSignal

from core.feature_extractor import CLIPFeatureExtractor
from config import settings
from utils.logger import get_logger

logger = get_logger("index_thread")

try:
    import faiss
except Exception:
    faiss = None


class IndexThread(QThread):
    """离线视频索引构建线程"""

    # === 信号 ===
    progress_updated = pyqtSignal(int, int)  # (当前帧序, 总帧数)
    index_built = pyqtSignal(str)            # 索引保存路径
    index_error = pyqtSignal(str)            # 错误信息

    def __init__(self, parent=None):
        super().__init__(parent)
        self._video_path: str = ""
        self._sample_fps: float = settings.VIDEO_INDEX_SAMPLE_FPS
        self._max_frames: Optional[int] = None
        self._index_path: Path = settings.INDEX_FILE
        self._mapping_path: Path = settings.MAPPING_FILE

        # 取消标志
        self._cancelled = False

    # ── 公共接口 ──────────────────────────────────────

    def setup(
        self,
        video_path: str,
        sample_fps: float = None,
        max_frames: int = None,
        index_path: Path = None,
        mapping_path: Path = None,
    ):
        """
        配置索引构建参数。
        主线程调用此方法后调用 start() 即开始构建。

        Args:
            video_path: 本地视频文件路径
            sample_fps: 每秒采样帧数
            max_frames: 最大采样帧数上限
            index_path: 索引输出路径
            mapping_path: 映射文件输出路径
        """
        self._video_path = video_path
        self._sample_fps = sample_fps or settings.VIDEO_INDEX_SAMPLE_FPS
        self._max_frames = max_frames or 1000
        self._index_path = index_path or settings.INDEX_FILE
        self._mapping_path = mapping_path or settings.MAPPING_FILE
        self._cancelled = False

    def cancel(self):
        """请求取消构建"""
        self._cancelled = True
        logger.info("IndexThread cancel requested")

    # ── QThread 生命周期 ──────────────────────────────

    def run(self):
        if faiss is None:
            self.index_error.emit("faiss 未安装")
            return

        if not self._video_path:
            self.index_error.emit("未指定视频文件")
            return

        try:
            self._build()
        except Exception as e:
            logger.exception("Index build failed: %s", e)
            self.index_error.emit(str(e))

    # ── 私有方法 ──────────────────────────────────────

    def _build(self):
        """核心构建流程"""
        from PIL import Image

        # 1. 打开视频
        cap = cv2.VideoCapture(self._video_path)
        if not cap.isOpened():
            self.index_error.emit(f"无法打开视频: {self._video_path}")
            return

        # 2. 获取视频信息
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        logger.info(
            "Indexing video: %s, fps=%.2f, total_frames=%d",
            self._video_path, fps, total_frames,
        )

        # 计算采样间隔
        interval = 1
        if fps > 0 and self._sample_fps > 0:
            interval = max(1, int(round(fps / self._sample_fps)))

        # 3. 加载 CLIP
        fe = CLIPFeatureExtractor(device=settings.get_device())
        if fe.model is None:
            self.index_error.emit("CLIP 模型未加载")
            cap.release()
            return

        # 4. 构建 FAISS 索引
        dim = 512  # ViT-B/32 输出维度
        quantizer = faiss.IndexFlatL2(dim)
        index = faiss.IndexFlatL2(dim)

        vectors = []
        metas = []
        frame_count = 0

        # 计算预计采样帧数用于进度
        estimated_samples = min(
            total_frames // interval if interval > 0 else total_frames,
            self._max_frames or 1000,
        )

        while True:
            # 检查取消标志
            if self._cancelled:
                logger.info("Index build cancelled at frame %d", frame_count)
                cap.release()
                return

            ret, frame = cap.read()
            if not ret:
                break

            frame_count += 1

            # 采样
            if frame_count % interval != 0:
                continue

            # 达到上限
            if len(vectors) >= (self._max_frames or 1000):
                break

            try:
                # BGR → RGB → PIL → CLIP
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb)
                emb = fe.encode_image(pil_img)
                vectors.append(emb)

                ts = frame_count / fps if fps > 0 else 0.0
                metas.append({
                    "source": "video",
                    "video": str(self._video_path),
                    "ts": float(ts),
                    "frame_index": frame_count,
                })

                # 进度信号（每 5 帧发送一次，避免信号风暴）
                if len(vectors) % 5 == 0 or len(vectors) == 1:
                    self.progress_updated.emit(len(vectors), estimated_samples)

            except Exception:
                logger.exception("Frame %d encoding failed", frame_count)
                continue

        cap.release()

        if self._cancelled:
            return

        if len(vectors) == 0:
            self.index_error.emit("没有成功编码任何帧")
            return

        # 5. 添加向量到索引
        arr = np.stack(vectors).astype("float32")
        index.add(arr)

        # 6. 保存索引
        try:
            faiss.write_index(index, str(self._index_path))
            with open(self._mapping_path, "w", encoding="utf-8") as f:
                json.dump(metas, f, ensure_ascii=False, indent=2)

            logger.info(
                "Index built: %d vectors saved to %s",
                len(vectors), self._index_path,
            )

            # 最终进度
            self.progress_updated.emit(len(vectors), len(vectors))

            # 构建成功
            self.index_built.emit(str(self._index_path))

        except Exception as e:
            self.index_error.emit(f"保存索引失败: {e}")
