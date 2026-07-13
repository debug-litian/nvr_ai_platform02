"""
SearchThread — 实时文搜图线程

继承 QThread，每次搜索新建一个线程实例。
负责：
- 接收当前帧（BGR ndarray）或使用实时索引
- CLIP 文本编码 + 图像编码
- 计算余弦相似度并排序
- 返回匹配结果列表

使用 torch.no_grad() 包裹推理，节省显存。

信号：
- search_started(): 搜索开始
- search_finished(list): 返回 [{"similarity": float, "timestamp": float, "frame": ndarray, "meta": dict}, ...]
- search_error(str): 错误信息

注意：本线程在使用历史索引搜索时不接收帧，只操作 FAISS；
接收帧的场景是"实时抽帧单帧搜图"。
"""
from typing import Optional, List, Dict

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

from core.feature_extractor import CLIPFeatureExtractor
from core.realtime_indexer import RealtimeIndexer, _bgr_to_pil
from config import settings
from utils.logger import get_logger

logger = get_logger("search_thread")

try:
    import faiss
except Exception:
    faiss = None


class SearchThread(QThread):
    """实时文搜图线程"""

    # === 信号 ===
    search_started = pyqtSignal()
    search_finished = pyqtSignal(list)   # list[dict]
    search_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text: str = ""
        self._top_k: int = settings.TOP_K
        self._frame: Optional[np.ndarray] = None
        self._timestamp: float = 0.0
        self._source: str = "history"  # "history" | "realtime"

        # 懒加载的 CLIP 提取器和实时索引引用
        self._fe: Optional[CLIPFeatureExtractor] = None
        self._realtime_indexer: Optional[RealtimeIndexer] = None

        # 历史索引路径
        self._index_path = settings.INDEX_FILE
        self._mapping_path = settings.MAPPING_FILE

        # 停止标志
        self._stopped = False

    # ── 公共接口 ──────────────────────────────────────

    def setup(
        self,
        text: str,
        top_k: int = None,
        source: str = "history",
        frame: np.ndarray = None,
        timestamp: float = 0.0,
        realtime_indexer: RealtimeIndexer = None,
    ):
        """
        配置搜索参数。
        主线程调用完此方法后调用 start() 即开始搜索。

        Args:
            text: 搜索文本
            top_k: 返回结果数
            source: "history" → 历史 FAISS 索引  |  "realtime" → 实时抽帧索引
            frame: 当前帧（实时模式下用于返回，不参与检索）。
                  历史模式下为空即可。
            timestamp: 当前帧时间戳
            realtime_indexer: RealtimeIndexer 实例（实时模式下必需）
        """
        self._text = text
        self._top_k = top_k or settings.TOP_K
        self._source = source
        self._frame = frame.copy() if frame is not None else None
        self._timestamp = timestamp
        self._realtime_indexer = realtime_indexer
        self._stopped = False

    def stop(self):
        """请求取消搜索"""
        self._stopped = True

    # ── QThread 生命周期 ──────────────────────────────

    def run(self):
        if not self._text:
            self.search_error.emit("搜索文本为空")
            return

        self.search_started.emit()

        try:
            if self._source == "realtime":
                results = self._search_realtime()
            else:
                results = self._search_history()

            if self._stopped:
                return

            self.search_finished.emit(results)

        except Exception as e:
            logger.exception("Search failed: %s", e)
            self.search_error.emit(str(e))

    # ── 私有方法 ──────────────────────────────────────

    def _get_fe(self) -> CLIPFeatureExtractor:
        """懒加载 CLIP 特征提取器"""
        if self._fe is None:
            self._fe = CLIPFeatureExtractor(device=settings.get_device())
        return self._fe

    def _search_realtime(self) -> List[Dict]:
        """
        实时抽帧搜索：
        - 如果传入了 realtime_indexer，直接用它的方法搜索
        - 否则对当前单帧做 CLIP 编码后与文本做余弦相似度
        """
        if self._realtime_indexer is not None:
            return self._realtime_indexer.search_text(self._text, top_k=self._top_k)

        # 回退：单帧与文本的相似度
        if self._frame is None:
            return []

        fe = self._get_fe()
        pil_img = _bgr_to_pil(self._frame)
        if pil_img is None:
            return []

        import torch
        with torch.no_grad():
            img_emb = fe.encode_image(pil_img)
            text_emb = fe.encode_text(self._text)

        # 余弦相似度（向量已归一化，直接点积）
        similarity = float(np.dot(img_emb, text_emb))

        return [
            {
                "similarity": similarity,
                "timestamp": self._timestamp,
                "frame": self._frame,
                "meta": {
                    "source": "realtime_single",
                    "ts": self._timestamp,
                },
            }
        ]

    def _search_history(self) -> List[Dict]:
        """历史 FAISS 索引搜索"""
        if faiss is None:
            raise RuntimeError("faiss 未安装")

        index_path = self._index_path
        mapping_path = self._mapping_path

        if not index_path.exists():
            raise RuntimeError(f"索引文件不存在: {index_path}")

        fe = self._get_fe()
        if fe.model is None:
            raise RuntimeError("CLIP 模型未加载")

        # 加载索引
        index = faiss.read_index(str(index_path))

        # 加载映射
        import json
        mapping = []
        if mapping_path.exists():
            with open(mapping_path, "r", encoding="utf-8") as f:
                mapping = json.load(f)

        # 文本编码 + 检索
        q = fe.encode_text(self._text)
        q = np.expand_dims(q, axis=0).astype("float32")

        D, I = index.search(q, self._top_k)

        results = []
        for dist, idx in zip(D[0], I[0]):
            if idx < 0 or idx >= len(mapping):
                continue
            meta = mapping[idx]
            results.append({
                "similarity": float(1.0 / (1.0 + dist)),
                "timestamp": float(meta.get("ts", 0.0)),
                "frame": None,  # 历史搜索不返回帧
                "meta": meta,
            })

        return results
