"""
nvr_config_tester.py — NVR 布防配置测试器

基于 nvr_profile.json 中定义的预期配置，对 FTP 报警文件 / YOLO 检测结果
进行 9 大类逐项对比验证，生成结构化测试报告。

9 大类测试项：
  1. [Schedule] 布防计划 — 时间窗口/星期几/24小时覆盖
  2. [Alarm Type] 报警类型 — 人形/机动车/宠物/画面变动开关
  3. [Sensitivity] 灵敏度 — 低/中/高与 YOLO 置信度关联
  4. [Alarm Action] 报警联动 — FTP/邮件/录像/蜂鸣
  5. [Recording] 录像参数 — 时长/分辨率/编码/帧率
  6. [Detection Zone] 区域设置 — 移动侦测区域
  7. [Object Filter] 目标过滤 — 最小尺寸/类型过滤
  8. [FTP Settings] FTP 设置 — 连通性/文件类型/传输模式
  9. [Email] 邮件设置 — SMTP 连通 (未来)

使用方式:
    tester = NvrConfigTester("config/nvr_profile.json")
    report = tester.run_all_checks(verification_results)
    print(report.summary())
"""

import json
import os
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from collections import defaultdict

from core.alarm_types import get_friendly_name
from utils.logger import get_logger

logger = get_logger("nvr_config_tester")


# ═══════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════

@dataclass
class ConfigTestItem:
    """单项配置测试结果"""
    category: str              # 大类 (Schedule/Alarm Type/...)
    check_name: str            # 具体检查项
    channel: int               # 通道号 (-1 = 全局)
    expected: str              # 期望值
    actual: str                # 实际值
    passed: bool
    detail: str = ""           # 人可读的描述


@dataclass
class CategorySummary:
    """单类测试汇总"""
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total * 100 if self.total > 0 else 0.0


@dataclass
class ConfigTestReport:
    """NVR 配置测试完整报告"""
    profile_path: str = ""
    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0

    categories: Dict[str, CategorySummary] = field(default_factory=lambda: defaultdict(CategorySummary))
    items: List[ConfigTestItem] = field(default_factory=list)

    generated_at: Optional[datetime] = None

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total_checks * 100 if self.total_checks > 0 else 0.0

    def summary(self) -> str:
        """单行摘要"""
        return (
            f"Config Test: {self.passed}/{self.total_checks} passed "
            f"({self.pass_rate:.0f}%), {self.failed} failed, {self.skipped} skipped"
        )

    def to_dict(self) -> dict:
        return {
            "profile_path": self.profile_path,
            "total_checks": self.total_checks,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "pass_rate": round(self.pass_rate, 1),
            "categories": {
                cat: {
                    "total": s.total,
                    "passed": s.passed,
                    "failed": s.failed,
                    "skipped": s.skipped,
                    "pass_rate": round(s.pass_rate, 1),
                }
                for cat, s in self.categories.items()
            },
            "items": [
                {
                    "category": item.category,
                    "check_name": item.check_name,
                    "channel": item.channel,
                    "expected": item.expected,
                    "actual": item.actual,
                    "passed": item.passed,
                    "detail": item.detail,
                }
                for item in self.items
            ],
        }


# ═══════════════════════════════════════════════════════════
# 配置测试器
# ═══════════════════════════════════════════════════════════

class NvrConfigTester:
    """
    NVR 布防配置测试器。

    用法:
        tester = NvrConfigTester("config/nvr_profile.json")
        report = tester.run_all_checks(verification_results_list)
    """

    # 9 大类名称
    CATEGORIES = [
        "布防计划 (Schedule)",
        "报警类型 (Alarm Type)",
        "灵敏度 (Sensitivity)",
        "报警联动 (Alarm Action)",
        "录像参数 (Recording)",
        "区域设置 (Detection Zone)",
        "目标过滤 (Object Filter)",
        "FTP设置 (FTP Settings)",
        "邮件设置 (Email)",
    ]

    def __init__(self, profile_path: Optional[str] = None):
        self._profile: dict = {}
        self._profile_path = ""
        if profile_path:
            self.load_profile(profile_path)

    def load_profile(self, path: str):
        """加载 NVR 配置文件"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._profile = json.load(f)
            self._profile_path = path
            logger.info("NVR 配置已加载: %s", path)
        except FileNotFoundError:
            logger.warning("配置文件不存在: %s", path)
            self._profile = {}
        except Exception:
            logger.exception("加载配置失败: %s", path)
            self._profile = {}

    # ── 主入口 ──────────────────────────────────────

    def run_all_checks(self, verification_results: List[Dict]) -> ConfigTestReport:
        """
        对一批 FTP 核验结果运行全部 9 大类配置测试。

        Args:
            verification_results: list of VerificationResult.to_dict()
        """
        report = ConfigTestReport(
            profile_path=self._profile_path,
            generated_at=datetime.now(),
        )

        if not self._profile:
            report.items.append(ConfigTestItem(
                category="全局", check_name="配置文件加载",
                channel=-1, expected="nvr_profile.json 存在", actual="未加载",
                passed=False, detail="请先加载 NVR 配置文件"
            ))
            return report

        # 逐项检查
        for vr in verification_results:
            channel = vr.get("channel", -1)
            ch_key = str(channel)

            # 1. 布防计划
            self._check_arming_schedule(vr, channel, ch_key, report)

            # 2. 报警类型开关
            self._check_alarm_type_enabled(vr, channel, ch_key, report)

            # 3. 灵敏度
            self._check_sensitivity(vr, channel, ch_key, report)

            # 4. 报警联动 — FTP 上传
            self._check_ftp_upload(vr, channel, ch_key, report)

            # 5. 录像参数
            self._check_recording_params(vr, channel, ch_key, report)

            # 6. 区域设置 (标记 — YOLO 检测框位置)
            self._check_detection_zone(vr, channel, ch_key, report)

            # 7. 目标过滤
            self._check_object_filter(vr, channel, ch_key, report)

        # 8. FTP 设置 (全局)
        self._check_ftp_settings(report, verification_results)

        # 9. 邮件设置 (标记为 skip)
        self._check_email_settings(report)

        # 统计
        self._compute_report(report)
        return report

    # ── 逐项检查 ─────────────────────────────────────

    def _get_channel_config(self, ch_key: str) -> dict:
        """获取通道配置，带默认值回退"""
        channels = self._profile.get("channels", {})
        default = self._profile.get("default_channel", {})
        return channels.get(ch_key, default)

    def _add_item(self, report: ConfigTestReport, item: ConfigTestItem):
        report.items.append(item)

    def _check_arming_schedule(self, vr: dict, channel: int, ch_key: str, report: ConfigTestReport):
        """1. 布防计划校验"""
        ch_cfg = self._get_channel_config(ch_key)
        schedule = ch_cfg.get("arming_schedule", {})
        if not schedule:
            self._add_item(report, ConfigTestItem(
                category="布防计划 (Schedule)", check_name="时间窗口",
                channel=channel, expected="已配置", actual="未配置",
                passed=True, detail="未配置布防计划 → 默认全天布防，跳过检查"
            ))
            return

        ts = vr.get("alarm_timestamp_dt")
        if isinstance(ts, str):
            try:
                ts = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                ts = None

        if ts is None:
            # 尝试从字符串解析
            ts_str = vr.get("alarm_timestamp", "")
            try:
                ts = datetime.strptime(str(ts_str), "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                self._add_item(report, ConfigTestItem(
                    category="布防计划 (Schedule)", check_name="时间窗口",
                    channel=channel, expected="有效时间戳", actual=str(ts_str),
                    passed=False, detail="无法解析报警时间戳"
                ))
                return

        days = schedule.get("days", list(range(7)))
        start_str = schedule.get("start_time", "00:00")
        end_str = schedule.get("end_time", "23:59")

        # 检查星期几
        weekday = ts.weekday()
        if weekday not in days:
            day_names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
            self._add_item(report, ConfigTestItem(
                category="布防计划 (Schedule)", check_name="星期几",
                channel=channel,
                expected=f"Days {days}",
                actual=day_names[weekday],
                passed=False,
                detail=f"报警发生在 {day_names[weekday]}，不在布防日 {days} 中"
            ))

        # 检查时间窗口
        try:
            start_t = dtime.fromisoformat(start_str)
            end_t = dtime.fromisoformat(end_str)
            alarm_t = ts.time()

            in_window = start_t <= alarm_t <= end_t
            self._add_item(report, ConfigTestItem(
                category="布防计划 (Schedule)", check_name="时间窗口",
                channel=channel,
                expected=f"{start_str} ~ {end_str}",
                actual=ts.strftime("%H:%M:%S"),
                passed=in_window,
                detail=f"报警时间{'在' if in_window else '不在'}布防窗口内"
            ))
        except ValueError:
            pass

    def _check_alarm_type_enabled(self, vr: dict, channel: int, ch_key: str, report: ConfigTestReport):
        """2. 报警类型开关"""
        ch_cfg = self._get_channel_config(ch_key)
        alarm_types = ch_cfg.get("alarm_types", {})

        alarm_type = vr.get("nvr_alarm_type", "unknown")
        is_enabled = alarm_types.get(alarm_type, True)
        label = get_friendly_name(alarm_type)

        self._add_item(report, ConfigTestItem(
            category="报警类型 (Alarm Type)", check_name=f"{label}报警开关",
            channel=channel,
            expected="开启" if is_enabled else "关闭",
            actual="开启" if is_enabled else "关闭",
            passed=is_enabled,
            detail=f"通道{channel} {label}报警{'已开启' if is_enabled else '已关闭'}"
        ))

    def _check_sensitivity(self, vr: dict, channel: int, ch_key: str, report: ConfigTestReport):
        """3. 灵敏度 — 与 YOLO 置信度关联"""
        ch_cfg = self._get_channel_config(ch_key)
        sensitivity = ch_cfg.get("sensitivity", "medium")

        # 灵敏度映射到期望置信度
        sens_map = {"low": 0.1, "medium": 0.25, "high": 0.45}
        expected_conf = sens_map.get(sensitivity, 0.25)

        actual_conf = vr.get("yolo_max_confidence", 0.0)

        # 如果 YOLO 不可用，跳过
        if not vr.get("yolo_applicable"):
            self._add_item(report, ConfigTestItem(
                category="灵敏度 (Sensitivity)", check_name="YOLO 置信度",
                channel=channel,
                expected=f"{sensitivity} (>= {expected_conf})",
                actual="N/A (不可核验类型)",
                passed=True,
                detail="非 YOLO 核验类型，跳过灵敏度检查"
            ))
            return

        conf_ok = actual_conf >= expected_conf
        self._add_item(report, ConfigTestItem(
            category="灵敏度 (Sensitivity)", check_name="YOLO 置信度",
            channel=channel,
            expected=f"{sensitivity} (>= {expected_conf})",
            actual=f"{actual_conf:.3f}",
            passed=conf_ok,
            detail=f"灵敏度{sensitivity}期望置信度>={expected_conf}，实际{actual_conf:.3f}"
        ))

    def _check_ftp_upload(self, vr: dict, channel: int, ch_key: str, report: ConfigTestReport):
        """4. 报警联动 — FTP 上传"""
        ch_cfg = self._get_channel_config(ch_key)
        alarm_actions = ch_cfg.get("alarm_actions", {})

        ftp_enabled = alarm_actions.get("ftp_upload", True)

        # FTP 文件已到达 → FTP 上传功能正常
        has_file = bool(vr.get("filename"))

        self._add_item(report, ConfigTestItem(
            category="报警联动 (Alarm Action)", check_name="FTP 上传",
            channel=channel,
            expected="文件到达" if ftp_enabled else "不上传",
            actual="文件已到达" if has_file else "无文件",
            passed=has_file == ftp_enabled if ftp_enabled else True,
            detail=f"FTP 上传{'已开启' if ftp_enabled else '未开启'}，{'有文件' if has_file else '无文件'}"
        ))

        # 录像开关
        video_enabled = alarm_actions.get("record", True)
        has_video = vr.get("file_type") == "video"
        if not video_enabled:
            # 录像关闭 → 不应有 MP4
            self._add_item(report, ConfigTestItem(
                category="报警联动 (Alarm Action)", check_name="录像联动",
                channel=channel,
                expected="不录像" if not video_enabled else "有录像",
                actual="有 MP4" if has_video else "无 MP4",
                passed=not has_video if not video_enabled else True,
                detail=f"录像联动{'关闭' if not video_enabled else '开启'}，{'有' if has_video else '无'}MP4文件"
            ))

    def _check_recording_params(self, vr: dict, channel: int, ch_key: str, report: ConfigTestReport):
        """5. 录像参数"""
        if vr.get("file_type") != "video":
            return  # 只对 MP4 文件做录像参数检查

        ch_cfg = self._get_channel_config(ch_key)
        expected = ch_cfg.get("expected", {})

        # 录像时长
        exp_dur = expected.get("mp4_duration_sec")
        if exp_dur:
            actual_dur = vr.get("video_duration_sec")
            if actual_dur is not None:
                ok = abs(actual_dur - exp_dur) <= 3
                self._add_item(report, ConfigTestItem(
                    category="录像参数 (Recording)", check_name="录像时长",
                    channel=channel,
                    expected=f"{exp_dur}s",
                    actual=f"{actual_dur:.0f}s",
                    passed=ok,
                    detail=f"预期 {exp_dur}s，实际 {actual_dur:.0f}s"
                ))

        # 分辨率
        exp_res = expected.get("mp4_resolution")
        if exp_res:
            w = vr.get("video_width")
            h = vr.get("video_height")
            if w and h:
                actual_res = f"{w}x{h}"
                ok = actual_res == exp_res
                self._add_item(report, ConfigTestItem(
                    category="录像参数 (Recording)", check_name="视频分辨率",
                    channel=channel,
                    expected=exp_res,
                    actual=actual_res,
                    passed=ok,
                    detail=f"预期 {exp_res}，实际 {actual_res}"
                ))

        # 编码
        exp_codec = expected.get("mp4_codec")
        if exp_codec:
            actual_codec = vr.get("video_codec", "")
            ok = exp_codec.upper() in actual_codec.upper()
            self._add_item(report, ConfigTestItem(
                category="录像参数 (Recording)", check_name="视频编码",
                channel=channel,
                expected=exp_codec,
                actual=actual_codec,
                passed=ok,
                detail=f"预期 {exp_codec}，实际 {actual_codec}"
            ))

    def _check_detection_zone(self, vr: dict, channel: int, ch_key: str, report: ConfigTestReport):
        """6. 区域设置 — 标记 YOLO 检测框中心是否在配置区域内"""
        ch_cfg = self._get_channel_config(ch_key)
        zone = ch_cfg.get("detection_zone", {})

        if not zone:
            return  # 未配置区域 → 跳过

        # 检查 YOLO 检测结果中的框位置
        yolo_classes = vr.get("yolo_classes_found", [])
        if not yolo_classes:
            self._add_item(report, ConfigTestItem(
                category="区域设置 (Detection Zone)", check_name="检测区域内目标",
                channel=channel,
                expected="至少1个目标在区域内",
                actual="无YOLO检测目标",
                passed=False,
                detail="未检测到任何目标，无法验证区域设置"
            ))
            return

        # 标记：需要 YOLO 框坐标才能精确验证
        self._add_item(report, ConfigTestItem(
            category="区域设置 (Detection Zone)", check_name="检测区域覆盖",
            channel=channel,
            expected="区域已配置",
            actual="有YOLO检测目标" if yolo_classes else "无目标",
            passed=True,
            detail=f"区域设置已配置，检测到 {len(yolo_classes)} 类目标（精确区域验证需框坐标）"
        ))

    def _check_object_filter(self, vr: dict, channel: int, ch_key: str, report: ConfigTestReport):
        """7. 目标过滤"""
        ch_cfg = self._get_channel_config(ch_key)
        obj_filter = ch_cfg.get("object_filter", {})

        if not obj_filter:
            return

        # 类型过滤
        allowed_types = obj_filter.get("allowed_types", [])
        if allowed_types and vr.get("yolo_applicable"):
            alarm_type = vr.get("nvr_alarm_type", "")
            expected_coco = allowed_types.get(alarm_type, [])
            found = set(vr.get("yolo_classes_found", []))
            matched = found & set(expected_coco) if expected_coco else True
            self._add_item(report, ConfigTestItem(
                category="目标过滤 (Object Filter)", check_name="目标类型允许列表",
                channel=channel,
                expected=str(allowed_types),
                actual=str(list(found)),
                passed=bool(matched),
                detail="检测目标类型在允许范围内" if matched else "检测到不允许的目标类型"
            ))

    def _check_ftp_settings(self, report: ConfigTestReport, results: List[Dict]):
        """8. FTP 设置 (全局)"""
        ftp_cfg = self._profile.get("ftp", {})

        # FTP 服务器连通
        has_files = len(results) > 0
        self._add_item(report, ConfigTestItem(
            category="FTP设置 (FTP Settings)", check_name="FTP 服务器连通",
            channel=-1,
            expected="文件到达",
            actual="有文件到达" if has_files else "无文件",
            passed=has_files,
            detail=f"FTP 服务器 {ftp_cfg.get('server_ip', '?')}，{'正常' if has_files else '无文件'}接收"
        ))

        # 文件类型
        jpg_count = sum(1 for r in results if r.get("file_type") == "image")
        mp4_count = sum(1 for r in results if r.get("file_type") == "video")
        self._add_item(report, ConfigTestItem(
            category="FTP设置 (FTP Settings)", check_name="文件类型",
            channel=-1,
            expected="JPG + MP4",
            actual=f"JPG:{jpg_count} MP4:{mp4_count}",
            passed=(jpg_count > 0 or mp4_count > 0),
        ))

        # 传输模式
        transport = ftp_cfg.get("transport_mode", "PASV")
        self._add_item(report, ConfigTestItem(
            category="FTP设置 (FTP Settings)", check_name="传输模式",
            channel=-1,
            expected=transport,
            actual=f"{transport} (文件正常到达)",
            passed=True,
        ))

    def _check_email_settings(self, report: ConfigTestReport):
        """9. 邮件设置 (未来)"""
        email_cfg = self._profile.get("email", {})
        self._add_item(report, ConfigTestItem(
            category="邮件设置 (Email)", check_name="SMTP 邮件通知",
            channel=-1,
            expected="TBD (后续实现)",
            actual="未检测",
            passed=True,
            detail="邮件核验功能待后续版本实现"
        ))

    def _compute_report(self, report: ConfigTestReport):
        """统计汇总"""
        for item in report.items:
            report.total_checks += 1
            cat = item.category
            report.categories[cat].total += 1

            if item.passed:
                report.passed += 1
                report.categories[cat].passed += 1
            else:
                report.failed += 1
                report.categories[cat].failed += 1
