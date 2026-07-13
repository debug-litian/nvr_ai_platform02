"""
alarm_verifier.py — 报警核验引擎

对单个 FTP 报警文件执行完整的核验流程：

1. 文件属性读取（图片分辨率 / 视频分辨率、时长、编码）
2. YOLO 目标检测（JPG 单帧 / MP4 抽帧）
3. 绿线花屏检测
4. 报警类型 vs YOLO 结果对比 → 判断误报
5. NVR 配置合规检查（布防时间、报警开关、预期参数）

设计为纯同步类，不继承 QThread — 由 VerificationWorker 在线程中调用。
"""

import os
import json
import time
import threading
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

import cv2
import numpy as np

from config import settings
from core.alarm_types import (
    get_expected_coco_ids,
    is_yolo_verifiable,
    get_coco_cn_name,
    get_friendly_name,
)
from detectors.green_line_detector import detect_green_and_vertical_lines
from utils.logger import get_logger

logger = get_logger("alarm_verifier")


# ═══════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════

@dataclass
class ConfigCheckResult:
    """单项配置检查结果"""
    check_name: str          # 检查项名称
    passed: bool             # 是否通过
    detail: str              # 人可读的描述
    expected: str            # 期望值
    actual: str              # 实际值


@dataclass
class YoloDetectionSummary:
    """YOLO 检测汇总"""
    classes_found: List[int] = field(default_factory=list)  # 检测到的 COCO class ID 列表（去重）
    max_confidence: float = 0.0       # 最高置信度
    total_detections: int = 0         # 总检测次数（MP4 抽帧模式下可能 >1）
    frames_with_detections: int = 0   # 有检测结果的帧数
    frames_sampled: int = 0           # 总采样帧数
    detections_per_class: Dict[int, int] = field(default_factory=dict)  # {class_id: count}
    raw_detections: List[List[float]] = field(default_factory=list)     # 所有原始检测结果


@dataclass
class FileProperties:
    """报警文件的物理属性"""
    file_size_bytes: int = 0
    file_size_mb: float = 0.0
    # 图片属性
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    # 视频属性
    video_width: Optional[int] = None
    video_height: Optional[int] = None
    video_fps: Optional[float] = None
    video_duration_sec: Optional[float] = None
    video_frame_count: Optional[int] = None
    video_codec: Optional[str] = None


@dataclass
class VerificationResult:
    """单个报警文件的完整核验结果"""
    # ── 输入 ────────────────────────────────────────
    file_path: str
    channel: int
    alarm_timestamp: datetime
    nvr_alarm_type: str            # 内部标识符 "human"/"vehicle"/"pet"/"motion"
    nvr_alarm_label: str           # 中文标签 "人形"/"机动车"/...
    filename: str
    file_type: str                 # "image" / "video"
    nvr_name: str = ""

    # ── 文件属性 ────────────────────────────────────
    file_props: FileProperties = field(default_factory=FileProperties)

    # ── YOLO 检测 ──────────────────────────────────
    yolo_applicable: bool = False          # 此类型是否适用 YOLO 核验
    yolo_summary: Optional[YoloDetectionSummary] = None
    yolo_match: bool = False              # 是否检测到期望目标
    is_false_alarm: bool = False          # 最终误报判定（NVR声称有但AI没发现）
    false_alarm_reason: str = ""          # 误报原因

    # ── 绿线检测 ────────────────────────────────────
    green_line_detected: bool = False
    green_line_ratio: float = 0.0

    # ── 配置检查 ────────────────────────────────────
    config_checks: List[ConfigCheckResult] = field(default_factory=list)
    config_all_pass: Optional[bool] = None   # None=没有配置文件

    # ── 处理元数据 ──────────────────────────────────
    processing_time_sec: float = 0.0
    error: Optional[str] = None
    verified_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """转为可序列化字典（用于信号传递和 JSON 序列化）"""
        d = {
            "file_path": self.file_path,
            "channel": self.channel,
            "alarm_timestamp": self.alarm_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "alarm_timestamp_dt": self.alarm_timestamp,
            "nvr_alarm_type": self.nvr_alarm_type,
            "nvr_alarm_label": self.nvr_alarm_label,
            "filename": self.filename,
            "file_type": self.file_type,
            "nvr_name": self.nvr_name,
            "file_size_mb": round(self.file_props.file_size_mb, 2),
            "image_width": self.file_props.image_width,
            "image_height": self.file_props.image_height,
            "video_width": self.file_props.video_width,
            "video_height": self.file_props.video_height,
            "video_duration_sec": self.file_props.video_duration_sec,
            "video_codec": self.file_props.video_codec,
            "yolo_applicable": self.yolo_applicable,
            "yolo_match": self.yolo_match,
            "is_false_alarm": self.is_false_alarm,
            "false_alarm_reason": self.false_alarm_reason,
            "green_line_detected": self.green_line_detected,
            "green_line_ratio": round(self.green_line_ratio, 4),
            "config_checks": [
                {
                    "check_name": c.check_name,
                    "passed": c.passed,
                    "detail": c.detail,
                    "expected": c.expected,
                    "actual": c.actual,
                }
                for c in self.config_checks
            ],
            "config_all_pass": self.config_all_pass,
            "processing_time_sec": round(self.processing_time_sec, 2),
            "error": self.error,
        }
        if self.yolo_summary:
            d["yolo_classes_found"] = self.yolo_summary.classes_found
            d["yolo_max_confidence"] = round(self.yolo_summary.max_confidence, 4)
            d["yolo_frames_sampled"] = self.yolo_summary.frames_sampled
            d["yolo_frames_with_detections"] = self.yolo_summary.frames_with_detections
            d["yolo_detections_per_class"] = {
                get_coco_cn_name(k): v
                for k, v in self.yolo_summary.detections_per_class.items()
            }
        return d


# ═══════════════════════════════════════════════════════════
# 文件属性读取
# ═══════════════════════════════════════════════════════════

def read_file_properties(file_path: str, file_type: str) -> FileProperties:
    """读取报警文件的物理属性"""
    props = FileProperties()

    try:
        stat = os.stat(file_path)
        props.file_size_bytes = stat.st_size
        props.file_size_mb = stat.st_size / (1024 * 1024)
    except OSError:
        pass

    if file_type == "image":
        _read_image_props(file_path, props)
    elif file_type == "video":
        _read_video_props(file_path, props)

    return props


def _read_image_props(path: str, props: FileProperties):
    """读取图片宽高"""
    try:
        img = cv2.imread(path)
        if img is not None:
            h, w = img.shape[:2]
            props.image_width = w
            props.image_height = h
    except Exception:
        pass


def _read_video_props(path: str, props: FileProperties):
    """读取视频宽高、帧率、时长、编码"""
    try:
        cap = cv2.VideoCapture(path)
        if cap.isOpened():
            props.video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            props.video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            props.video_fps = float(cap.get(cv2.CAP_PROP_FPS))
            props.video_frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if props.video_fps > 0 and props.video_frame_count > 0:
                props.video_duration_sec = round(
                    props.video_frame_count / props.video_fps, 1
                )
            # 获取编码（fourcc）
            fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
            props.video_codec = "".join(
                [chr((fourcc >> 8 * i) & 0xFF) for i in range(4)]
            )
            cap.release()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# 报警核验器
# ═══════════════════════════════════════════════════════════

class AlarmVerifier:
    """
    报警核验引擎。

    对单个 FTP 报警文件执行 YOLO 检测 + 配置校验，
    生成结构化 VerificationResult。

    不是 QThread — 由外部线程调用。
    """

    def __init__(self, detector=None, profile_path: Optional[str] = None):
        """
        参数:
            detector: YoloDetector 实例（可共享）
            profile_path: nvr_profile.json 路径
        """
        self.detector = detector
        self._profile: Optional[Dict] = None
        self._lock = threading.Lock()

        if profile_path:
            self._load_profile(profile_path)

    def set_detector(self, detector):
        """设置/更换 YOLO 检测器"""
        self.detector = detector

    def _load_profile(self, path: str):
        """加载 NVR 配置文件"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._profile = json.load(f)
            logger.info("NVR 配置已加载: %s", path)
        except FileNotFoundError:
            logger.warning("NVR 配置文件不存在: %s", path)
            self._profile = None
        except Exception:
            logger.exception("加载 NVR 配置失败: %s", path)
            self._profile = None

    # ── 主入口 ──────────────────────────────────────

    def verify(self, record: Dict) -> VerificationResult:
        """
        核验单个报警记录。

        参数:
            record: parse_filename() 返回的字典，必须包含:
                full_path, channel, timestamp, alarm_type, file_type, filename

        返回:
            VerificationResult（含 all fields）
        """
        t0 = time.time()

        file_path = record.get("full_path", "")
        channel = record.get("channel", -1)
        timestamp = record.get("timestamp", datetime.now())
        alarm_type = record.get("alarm_type", "unknown")
        file_type = record.get("file_type", "unknown")
        filename = record.get("original", os.path.basename(file_path))
        nvr_name = record.get("nvr_name", "")

        result = VerificationResult(
            file_path=file_path,
            channel=channel,
            alarm_timestamp=timestamp,
            nvr_alarm_type=alarm_type,
            nvr_alarm_label=get_friendly_name(alarm_type),
            filename=filename,
            file_type=file_type,
            nvr_name=nvr_name,
        )

        # ── 1. 文件属性 ──────────────────────────────
        result.file_props = read_file_properties(file_path, file_type)

        # ── 2. 绿线检测（图片和视频首帧）─────────────
        self._check_green_line(file_path, file_type, result)

        # ── 3. YOLO 检测 ─────────────────────────────
        result.yolo_applicable = is_yolo_verifiable(alarm_type)
        if result.yolo_applicable and self.detector is not None:
            self._run_yolo_check(file_path, file_type, alarm_type, result)

        # ── 4. 配置校验 ──────────────────────────────
        if self._profile:
            result.config_checks = self._run_config_checks(
                channel, alarm_type, timestamp, result.file_props, file_type
            )
            result.config_all_pass = all(c.passed for c in result.config_checks)

        # ── 5. 误报判定 ──────────────────────────────
        self._judge_false_alarm(result)

        result.processing_time_sec = time.time() - t0
        result.verified_at = datetime.now()

        logger.info(
            "核验完成: %s ch=%d type=%s 误报=%s 耗时=%.2fs",
            filename, channel, alarm_type, result.is_false_alarm,
            result.processing_time_sec,
        )

        return result

    # ── YOLO 检测 ───────────────────────────────────

    def _run_yolo_check(
        self, file_path: str, file_type: str, alarm_type: str,
        result: VerificationResult,
    ):
        """对图片或视频运行 YOLO 检测"""
        expected_classes = get_expected_coco_ids(alarm_type)
        summary = YoloDetectionSummary()

        try:
            if file_type == "image":
                self._yolo_on_image(file_path, summary)
            elif file_type == "video":
                self._yolo_on_video(file_path, summary)

            # 判定是否匹配
            matched = set(summary.classes_found) & set(expected_classes)
            result.yolo_match = len(matched) > 0

        except Exception:
            logger.exception("YOLO 检测异常: %s", file_path)
            result.error = "YOLO 检测异常"

        result.yolo_summary = summary

    def _yolo_on_image(self, path: str, summary: YoloDetectionSummary):
        """对单张图片运行 YOLO"""
        frame = cv2.imread(path)
        if frame is None:
            return

        with self._lock:
            detections = self.detector.detect(frame)

        summary.frames_sampled = 1
        summary.raw_detections = detections

        classes = set()
        for d in detections:
            cls_id = int(d[5])
            conf = float(d[4])
            classes.add(cls_id)
            summary.detections_per_class[cls_id] = (
                summary.detections_per_class.get(cls_id, 0) + 1
            )
            if conf > summary.max_confidence:
                summary.max_confidence = conf

        summary.classes_found = list(classes)
        summary.total_detections = len(detections)
        summary.frames_with_detections = 1 if detections else 0

    def _yolo_on_video(self, path: str, summary: YoloDetectionSummary):
        """对视频文件抽帧运行 YOLO"""
        try:
            from ultralytics import YOLO as UltralyticsYOLO
        except ImportError:
            # fallback: 手动抽帧
            self._yolo_on_video_manual(path, summary)
            return

        # 使用 ultralytics 原生视频流模式（stream=True）
        try:
            cap = cv2.VideoCapture(path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
            cap.release()

            sample_fps = settings.ALARM_VIDEO_SAMPLE_FPS
            vid_stride = max(1, int(fps / sample_fps))

            with self._lock:
                results = self.detector.model.predict(
                    path,
                    imgsz=640,
                    conf=settings.YOLO_CONFIDENCE_THRESHOLD,
                    stream=True,
                    vid_stride=vid_stride,
                    verbose=False,
                )

            classes = set()
            all_dets = []
            frame_count = 0
            frames_with_dets = 0

            for r in results:
                frame_count += 1
                boxes = r.boxes
                frame_has_det = False
                if boxes is not None:
                    for b in boxes:
                        cls_id = int(b.cls[0].cpu().numpy())
                        conf = float(b.conf[0].cpu().numpy())
                        xyxy = b.xyxy[0].cpu().numpy().tolist()
                        classes.add(cls_id)
                        all_dets.append(xyxy + [conf, cls_id])
                        summary.detections_per_class[cls_id] = (
                            summary.detections_per_class.get(cls_id, 0) + 1
                        )
                        if conf > summary.max_confidence:
                            summary.max_confidence = conf
                        frame_has_det = True
                if frame_has_det:
                    frames_with_dets += 1

            summary.classes_found = list(classes)
            summary.total_detections = len(all_dets)
            summary.frames_sampled = frame_count
            summary.frames_with_detections = frames_with_dets
            summary.raw_detections = all_dets

        except Exception:
            logger.exception("ultralytics 视频流模式失败，回退手动抽帧")
            self._yolo_on_video_manual(path, summary)

    def _yolo_on_video_manual(self, path: str, summary: YoloDetectionSummary):
        """手动抽帧（OpenCV + YOLO），作为 fallback"""
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return

        fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
        interval = max(1, int(fps / settings.ALARM_VIDEO_SAMPLE_FPS))
        frame_idx = 0
        classes = set()
        all_dets = []
        frame_count = 0
        frames_with_dets = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % interval == 0:
                frame_count += 1
                with self._lock:
                    dets = self.detector.detect(frame)

                frame_has_det = False
                for d in dets:
                    cls_id = int(d[5])
                    conf = float(d[4])
                    classes.add(cls_id)
                    all_dets.append(d)
                    summary.detections_per_class[cls_id] = (
                        summary.detections_per_class.get(cls_id, 0) + 1
                    )
                    if conf > summary.max_confidence:
                        summary.max_confidence = conf
                    frame_has_det = True
                if frame_has_det:
                    frames_with_dets += 1

            frame_idx += 1

        cap.release()

        summary.classes_found = list(classes)
        summary.total_detections = len(all_dets)
        summary.frames_sampled = frame_count
        summary.frames_with_detections = frames_with_dets
        summary.raw_detections = all_dets

    # ── 绿线检测 ────────────────────────────────────

    def _check_green_line(
        self, file_path: str, file_type: str, result: VerificationResult,
    ):
        """检测绿线/花屏"""
        frame = None
        try:
            if file_type == "image":
                frame = cv2.imread(file_path)
            elif file_type == "video":
                cap = cv2.VideoCapture(file_path)
                if cap.isOpened():
                    ret, f = cap.read()
                    if ret:
                        frame = f
                    cap.release()

            if frame is not None:
                g = detect_green_and_vertical_lines(frame)
                if g.get("green_ratio", 0) > settings.GREEN_LINE_THRESHOLD:
                    result.green_line_detected = True
                result.green_line_ratio = g.get("green_ratio", 0.0)
        except Exception:
            pass

    # ── 误报判定 ────────────────────────────────────

    def _judge_false_alarm(self, result: VerificationResult):
        """综合判定是否为误报"""
        # 画面变动类型：没有 YOLO 核验，绿线说明可能是花屏误报
        if not result.yolo_applicable:
            if result.green_line_detected:
                result.is_false_alarm = True
                result.false_alarm_reason = "画面变动+绿线花屏，疑似误报"
            else:
                result.is_false_alarm = False
                result.false_alarm_reason = ""
            return

        # YOLO 核验类型：检测结果 vs 期望
        if result.yolo_summary is None:
            result.is_false_alarm = False
            result.false_alarm_reason = ""
            return

        if result.yolo_match:
            result.is_false_alarm = False
            result.false_alarm_reason = ""
        else:
            # 没有检测到期望目标 → 误报
            expected_names = [
                get_coco_cn_name(c)
                for c in get_expected_coco_ids(result.nvr_alarm_type)
            ]
            found_names = [
                get_coco_cn_name(c)
                for c in result.yolo_summary.classes_found
            ] if result.yolo_summary.classes_found else ["(无目标)"]

            result.is_false_alarm = True
            result.false_alarm_reason = (
                f"NVR声称检测到 {result.nvr_alarm_label}，"
                f"但 AI 只发现: {', '.join(found_names)}，"
                f"期望: {', '.join(expected_names)}"
            )

    # ── 配置校验 ────────────────────────────────────

    def _run_config_checks(
        self,
        channel: int,
        alarm_type: str,
        timestamp: datetime,
        props: FileProperties,
        file_type: str,
    ) -> List[ConfigCheckResult]:
        """运行所有 NVR 配置校验"""
        checks = []

        # 获取通道配置
        ch_key = str(channel)
        channels = self._profile.get("channels", {})
        default = self._profile.get("default_channel", {})
        ch_cfg = channels.get(ch_key, default)

        # 1. 报警类型开关
        alarm_types = ch_cfg.get("alarm_types", {})
        is_enabled = alarm_types.get(alarm_type, True)
        checks.append(ConfigCheckResult(
            check_name="报警类型开关",
            passed=is_enabled,
            detail=f"通道{channel} {get_friendly_name(alarm_type)}报警{'已开启' if is_enabled else '已关闭'}",
            expected="已开启",
            actual="已开启" if is_enabled else "已关闭",
        ))

        # 2. 布防时间
        schedule = ch_cfg.get("arming_schedule", {})
        checks.append(self._check_schedule(timestamp, schedule, channel))

        # 3. 视频参数校验（仅对 MP4 文件）
        expected = ch_cfg.get("expected", {})
        if file_type == "video":
            checks.extend(self._check_video_params(props, expected, channel))

        # 4. FTP 连接检查（全局）
        checks.append(ConfigCheckResult(
            check_name="FTP传输",
            passed=True,
            detail="文件已成功到达 FTP 服务器",
            expected="文件正常到达",
            actual="文件正常到达",
        ))

        return checks

    def _check_schedule(
        self, ts: datetime, schedule: dict, channel: int,
    ) -> ConfigCheckResult:
        """校验报警时间是否在布防计划内"""
        if not schedule:
            return ConfigCheckResult(
                check_name="布防计划",
                passed=True,
                detail="未配置布防计划（默认全天布防）",
                expected="—",
                actual="—",
            )

        days = schedule.get("days", [0, 1, 2, 3, 4, 5, 6])
        start_str = schedule.get("start_time", "00:00")
        end_str = schedule.get("end_time", "23:59")

        weekday = ts.weekday()  # 0=周一 ... 6=周日
        alarm_time = ts.time()

        try:
            start = dtime.fromisoformat(start_str)
            end = dtime.fromisoformat(end_str)
        except ValueError:
            return ConfigCheckResult(
                check_name="布防计划",
                passed=True,
                detail=f"无法解析时间格式: {start_str}~{end_str}",
                expected=f"{start_str}~{end_str}",
                actual=ts.strftime("%H:%M:%S"),
            )

        in_days = weekday in days
        in_time = start <= alarm_time <= end
        passed = in_days and in_time

        day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return ConfigCheckResult(
            check_name="布防计划",
            passed=passed,
            detail=(
                f"报警时间 {ts.strftime('%Y-%m-%d %H:%M')} ({day_names[weekday]}) "
                f"{'在' if passed else '不在'}布防范围内"
            ),
            expected=f"{day_names[days[0]]}~{day_names[days[-1]]} {start_str}~{end_str}"
            if days else "全天",
            actual=f"{day_names[weekday]} {ts.strftime('%H:%M:%S')}",
        )

    def _check_video_params(
        self, props: FileProperties, expected: dict, channel: int,
    ) -> List[ConfigCheckResult]:
        """校验视频文件参数"""
        checks = []

        # 分辨率
        exp_res = expected.get("mp4_resolution")
        if exp_res and props.video_width and props.video_height:
            actual_res = f"{props.video_width}x{props.video_height}"
            checks.append(ConfigCheckResult(
                check_name="视频分辨率",
                passed=actual_res == exp_res,
                detail=f"通道{channel} 视频分辨率 {actual_res}",
                expected=exp_res,
                actual=actual_res,
            ))

        # 时长
        exp_dur = expected.get("mp4_duration_sec")
        if exp_dur and props.video_duration_sec is not None:
            delta = abs(props.video_duration_sec - exp_dur)
            passed = delta <= 3  # 允许 ±3 秒误差
            checks.append(ConfigCheckResult(
                check_name="视频时长",
                passed=passed,
                detail=f"通道{channel} 视频时长 {props.video_duration_sec}s",
                expected=f"{exp_dur}s",
                actual=f"{props.video_duration_sec}s",
            ))

        # 编码
        exp_codec = expected.get("mp4_codec")
        if exp_codec and props.video_codec:
            checks.append(ConfigCheckResult(
                check_name="视频编码",
                passed=exp_codec.upper() in props.video_codec.upper(),
                detail=f"通道{channel} 编码: {props.video_codec}",
                expected=exp_codec,
                actual=props.video_codec,
            ))

        return checks
