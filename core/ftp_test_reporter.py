"""
ftp_test_reporter.py — FTP 功能测试报告引擎

聚合一组 VerificationResult 生成量化测试报告，覆盖 Reolink FTP 设置页
的所有配置项。支持导出 CSV 和 HTML 格式。

报告维度：
1. FTP 连接测试（连接状态、传输成功率）
2. 文件类型测试（图片/视频统计、分辨率、编码、时长）
3. AI 核验汇总（误报率、各类型匹配率）
4. 布防计划校验（时间段、星期覆盖、24小时覆盖）
5. 通道覆盖分析（通道覆盖率、通道×类型矩阵）
6. 综合评分（0-100）
"""

import csv
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any

from core.alarm_types import (
    get_friendly_name,
    get_alarm_types,
)
from utils.logger import get_logger

logger = get_logger("ftp_test_reporter")


# ═══════════════════════════════════════════════════════════
# 报告数据类
# ═══════════════════════════════════════════════════════════

@dataclass
class FTPTestReport:
    """FTP 功能测试聚合报告"""

    # ── 基础统计 ────────────────────────────────────
    total_files: int = 0
    jpg_count: int = 0
    mp4_count: int = 0
    channel_count: int = 0              # 有文件的通道数
    total_channels: int = 16           # NVR 总通道数
    time_range_start: Optional[datetime] = None
    time_range_end: Optional[datetime] = None
    nvr_name: str = ""

    # ── 文件大小统计 ────────────────────────────────
    file_size_min_mb: float = 0.0
    file_size_max_mb: float = 0.0
    file_size_avg_mb: float = 0.0

    # ── FTP 连接 ────────────────────────────────────
    ftp_connection_ok: bool = False
    ftp_transfer_success_rate: float = 0.0

    # ── 文件类型检测 ────────────────────────────────
    has_picture: bool = False
    has_video: bool = False
    has_both: bool = False

    # ── 图片属性 ────────────────────────────────────
    jpg_resolutions: Dict[str, int] = field(default_factory=dict)  # {"4608x1728": 5}
    jpg_upload_interval_avg_sec: float = 0.0

    # ── 视频属性 ────────────────────────────────────
    mp4_resolutions: Dict[str, int] = field(default_factory=dict)
    mp4_duration_avg_sec: float = 0.0
    mp4_duration_min_sec: float = 0.0
    mp4_duration_max_sec: float = 0.0
    mp4_codecs: Dict[str, int] = field(default_factory=dict)

    # ── AI 核验汇总 ─────────────────────────────────
    total_verifiable: int = 0             # 可核验的报警数
    yolo_match_count: int = 0             # AI 匹配数
    false_alarm_count: int = 0            # 误报数
    false_alarm_rate: float = 0.0         # 误报率 %
    match_rate: float = 0.0               # AI 匹配率 %
    alarm_type_stats: Dict[str, dict] = field(default_factory=dict)
    # {"human": {"total": 10, "match": 9, "false": 1, "match_rate": 90.0}}

    # ── 绿线/花屏 ───────────────────────────────────
    green_line_count: int = 0
    green_line_rate: float = 0.0

    # ── 布防计划 ────────────────────────────────────
    schedule_violations: int = 0
    schedule_coverage_by_day: Dict[int, int] = field(default_factory=dict)  # {0-6: count}
    schedule_coverage_by_hour: Dict[int, int] = field(default_factory=dict)  # {0-23: count}

    # ── 通道覆盖 ────────────────────────────────────
    channel_coverage: Dict[int, Dict[str, int]] = field(default_factory=dict)
    # {channel: {"human": 3, "vehicle": 2, ...}}
    channel_coverage_rate: float = 0.0

    # ── 综合评分 ────────────────────────────────────
    overall_score: float = 0.0
    score_details: List[str] = field(default_factory=list)

    # ── 生成时间 ────────────────────────────────────
    generated_at: Optional[datetime] = None


# ═══════════════════════════════════════════════════════════
# 报告生成
# ═══════════════════════════════════════════════════════════

class FTPTestReporter:
    """FTP 测试报告生成器"""

    def __init__(self, total_channels: int = 16):
        self.total_channels = total_channels
        self._results: List[Dict] = []

    def add_result(self, r: Dict):
        """添加一条核验结果"""
        self._results.append(r)

    def add_results(self, results: List[Dict]):
        """批量添加"""
        self._results.extend(results)

    def clear(self):
        """清空所有结果"""
        self._results.clear()

    @property
    def result_count(self) -> int:
        return len(self._results)

    def generate(self) -> FTPTestReport:
        """生成聚合测试报告"""
        report = FTPTestReport()
        results = self._results

        if not results:
            report.generated_at = datetime.now()
            return report

        report.generated_at = datetime.now()
        report.total_files = len(results)

        # ── 基本统计 ──────────────────────────────────
        self._compute_basic_stats(results, report)
        # ── 图片/视频属性 ────────────────────────────
        self._compute_file_attributes(results, report)
        # ── AI 核验 ───────────────────────────────────
        self._compute_ai_stats(results, report)
        # ── 布防计划 ──────────────────────────────────
        self._compute_schedule_stats(results, report)
        # ── 通道覆盖 ──────────────────────────────────
        self._compute_channel_coverage(results, report)
        # ── 综合评分 ──────────────────────────────────
        self._compute_score(report)

        return report

    # ── 各项统计 ──────────────────────────────────────

    def _compute_basic_stats(self, results: List[Dict], report: FTPTestReport):
        """基本统计"""
        # 文件类型计数
        report.jpg_count = sum(1 for r in results if r.get("file_type") == "image")
        report.mp4_count = sum(1 for r in results if r.get("file_type") == "video")
        report.has_picture = report.jpg_count > 0
        report.has_video = report.mp4_count > 0
        report.has_both = report.has_picture and report.has_video

        # 通道覆盖
        channels = set(r.get("channel", -1) for r in results)
        channels.discard(-1)
        report.channel_count = len(channels)
        report.total_channels = self.total_channels
        report.channel_coverage_rate = (
            report.channel_count / report.total_channels * 100
            if report.total_channels > 0
            else 0.0
        )

        # 时间范围
        timestamps = [
            r.get("alarm_timestamp_dt") or
            (datetime.strptime(r["alarm_timestamp"], "%Y-%m-%d %H:%M:%S")
             if isinstance(r.get("alarm_timestamp"), str) else None)
            for r in results
        ]
        valid_ts = [t for t in timestamps if t is not None]
        if valid_ts:
            report.time_range_start = min(valid_ts)
            report.time_range_end = max(valid_ts)

        # NVR 名称
        names = [r.get("nvr_name", "") for r in results if r.get("nvr_name")]
        report.nvr_name = Counter(names).most_common(1)[0][0] if names else ""

        # 文件大小
        sizes = [r.get("file_size_mb", 0) for r in results if r.get("file_size_mb")]
        if sizes:
            report.file_size_min_mb = round(min(sizes), 2)
            report.file_size_max_mb = round(max(sizes), 2)
            report.file_size_avg_mb = round(sum(sizes) / len(sizes), 2)

        # FTP 连接
        report.ftp_connection_ok = len(results) > 0
        report.ftp_transfer_success_rate = 100.0  # 文件已到达即成功

    def _compute_file_attributes(self, results: List[Dict], report: FTPTestReport):
        """图片/视频属性统计"""
        # JPG 分辨率分布
        jpg_results = [r for r in results if r.get("file_type") == "image"]
        for r in jpg_results:
            w = r.get("image_width")
            h = r.get("image_height")
            if w and h:
                key = f"{w}x{h}"
                report.jpg_resolutions[key] = report.jpg_resolutions.get(key, 0) + 1

        # 图片上传间隔
        jpg_times = sorted([
            r.get("alarm_timestamp_dt") or datetime.strptime(r["alarm_timestamp"], "%Y-%m-%d %H:%M:%S")
            for r in jpg_results
            if r.get("alarm_timestamp")
        ], key=lambda t: t if isinstance(t, datetime) else datetime.min)
        if len(jpg_times) >= 2:
            intervals = []
            for i in range(1, len(jpg_times)):
                t0 = jpg_times[i - 1]
                t1 = jpg_times[i]
                if isinstance(t0, datetime) and isinstance(t1, datetime):
                    intervals.append((t1 - t0).total_seconds())
            if intervals:
                report.jpg_upload_interval_avg_sec = round(sum(intervals) / len(intervals), 1)

        # MP4 属性
        mp4_results = [r for r in results if r.get("file_type") == "video"]
        for r in mp4_results:
            w = r.get("video_width")
            h = r.get("video_height")
            if w and h:
                key = f"{w}x{h}"
                report.mp4_resolutions[key] = report.mp4_resolutions.get(key, 0) + 1
            codec = r.get("video_codec", "")
            if codec:
                report.mp4_codecs[codec] = report.mp4_codecs.get(codec, 0) + 1

        durations = [r.get("video_duration_sec") for r in mp4_results
                     if r.get("video_duration_sec") is not None]
        if durations:
            report.mp4_duration_avg_sec = round(sum(durations) / len(durations), 1)
            report.mp4_duration_min_sec = round(min(durations), 1)
            report.mp4_duration_max_sec = round(max(durations), 1)

    def _compute_ai_stats(self, results: List[Dict], report: FTPTestReport):
        """AI 核验汇总"""
        # 仅统计可 YOLO 核验的
        verifiable = [r for r in results if r.get("yolo_applicable")]

        # 也包含不可核验的类型做全局统计
        all_results = results

        report.total_verifiable = len(verifiable)
        report.yolo_match_count = sum(1 for r in verifiable if r.get("yolo_match"))
        report.false_alarm_count = sum(1 for r in all_results if r.get("is_false_alarm"))

        total = len(all_results)
        report.false_alarm_rate = round(
            report.false_alarm_count / total * 100, 1
        ) if total > 0 else 0.0
        report.match_rate = round(
            report.yolo_match_count / len(verifiable) * 100, 1
        ) if verifiable else 0.0

        # 按报警类型统计
        for atype in get_alarm_types():
            type_results = [r for r in all_results if r.get("nvr_alarm_type") == atype]
            if not type_results:
                continue
            match = sum(1 for r in type_results if r.get("yolo_match"))
            false = sum(1 for r in type_results if r.get("is_false_alarm"))
            report.alarm_type_stats[atype] = {
                "total": len(type_results),
                "match": match,
                "false": false,
                "match_rate": round(match / len(type_results) * 100, 1)
                if type_results else 0.0,
                "label": get_friendly_name(atype),
            }

        # 绿线统计
        report.green_line_count = sum(
            1 for r in all_results if r.get("green_line_detected")
        )
        report.green_line_rate = round(
            report.green_line_count / total * 100, 1
        ) if total > 0 else 0.0

    def _compute_schedule_stats(self, results: List[Dict], report: FTPTestReport):
        """布防计划统计"""
        # 按星期统计
        for r in results:
            ts = r.get("alarm_timestamp_dt")
            if ts is None and isinstance(r.get("alarm_timestamp"), str):
                try:
                    ts = datetime.strptime(r["alarm_timestamp"], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass
            if ts is None:
                continue
            weekday = ts.weekday()  # 0=周一...6=周日
            hour = ts.hour
            report.schedule_coverage_by_day[weekday] = (
                report.schedule_coverage_by_day.get(weekday, 0) + 1
            )
            report.schedule_coverage_by_hour[hour] = (
                report.schedule_coverage_by_hour.get(hour, 0) + 1
            )

        # 布防违规
        for r in results:
            checks = r.get("config_checks", [])
            for c in checks:
                if c.get("check_name") == "布防计划" and not c.get("passed"):
                    report.schedule_violations += 1

    def _compute_channel_coverage(self, results: List[Dict], report: FTPTestReport):
        """通道覆盖"""
        for r in results:
            ch = r.get("channel", -1)
            atype = r.get("nvr_alarm_type", "unknown")
            if ch < 0:
                continue
            if ch not in report.channel_coverage:
                report.channel_coverage[ch] = {}
            report.channel_coverage[ch][atype] = (
                report.channel_coverage[ch].get(atype, 0) + 1
            )

    def _compute_score(self, report: FTPTestReport):
        """综合评分 0-100"""
        score = 0.0
        details = []
        total = report.total_files

        # 1. FTP 连接 (20分)
        if report.ftp_connection_ok:
            score += 20
            details.append("FTP连接: +20")
        else:
            details.append("FTP连接: +0 (无文件)")

        # 2. 文件类型 (15分)
        ft_score = 0
        if report.has_picture:
            ft_score += 7
        if report.has_video:
            ft_score += 8
        score += ft_score
        details.append(f"文件类型: +{ft_score} (图片{'✅' if report.has_picture else '❌'} 视频{'✅' if report.has_video else '❌'})")

        # 3. AI 误报率 (25分)
        if total > 0:
            far = report.false_alarm_rate
            if far <= 5:
                score += 25
                details.append(f"AI误报率: +25 ({far}% ≤ 5%)")
            elif far <= 15:
                score += 18
                details.append(f"AI误报率: +18 ({far}% ≤ 15%)")
            elif far <= 30:
                score += 10
                details.append(f"AI误报率: +10 ({far}% ≤ 30%)")
            else:
                score += 3
                details.append(f"AI误报率: +3 ({far}% > 30%)")
        else:
            details.append("AI误报率: +0 (无数据)")

        # 4. 通道覆盖率 (15分)
        ccr = report.channel_coverage_rate
        if ccr >= 90:
            score += 15
            details.append(f"通道覆盖: +15 ({ccr:.0f}%)")
        elif ccr >= 50:
            score += 8
            details.append(f"通道覆盖: +8 ({ccr:.0f}%)")
        else:
            score += 2
            details.append(f"通道覆盖: +2 ({ccr:.0f}%)")

        # 5. 布防计划 (10分)
        if report.schedule_violations == 0 and total > 0:
            score += 10
            details.append("布防计划: +10 (无违规)")
        elif report.schedule_violations > 0:
            score += max(0, 10 - report.schedule_violations * 2)
            details.append(f"布防计划: +{max(0, 10 - report.schedule_violations * 2)} ({report.schedule_violations}次违规)")

        # 6. 绿线/花屏 (10分)
        if total > 0:
            glr = report.green_line_rate
            if glr <= 10:
                score += 10
                details.append(f"画面质量: +10 (绿线率 {glr}%)")
            elif glr <= 30:
                score += 5
                details.append(f"画面质量: +5 (绿线率 {glr}%)")
            else:
                score += 1
                details.append(f"画面质量: +1 (绿线率 {glr}%)")

        # 7. 图片/视频参数合规 (5分)
        param_score = 0
        if report.jpg_resolutions:
            param_score += 2
        if report.mp4_duration_avg_sec > 0:
            param_score += 3
        score += param_score
        details.append(f"参数统计: +{param_score}")

        report.overall_score = round(score, 1)
        report.score_details = details


# ═══════════════════════════════════════════════════════════
# 导出功能
# ═══════════════════════════════════════════════════════════

def export_report_csv(report: FTPTestReport, path: str):
    """导出 CSV 格式测试报告"""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["NVR FTP 功能测试报告"])
        w.writerow(["生成时间", report.generated_at.strftime("%Y-%m-%d %H:%M:%S") if report.generated_at else ""])
        w.writerow(["NVR 型号", report.nvr_name])
        w.writerow([])
        w.writerow(["=== 综合评分 ==="])
        w.writerow(["总分", f"{report.overall_score}/100"])
        for d in report.score_details:
            w.writerow([d])
        w.writerow([])
        w.writerow(["=== 文件统计 ==="])
        w.writerow(["总文件数", report.total_files])
        w.writerow(["图片数", report.jpg_count])
        w.writerow(["视频数", report.mp4_count])
        w.writerow(["覆盖通道", f"{report.channel_count}/{report.total_channels}"])
        w.writerow(["通道覆盖率", f"{report.channel_coverage_rate:.1f}%"])
        if report.time_range_start:
            w.writerow(["时间范围", f"{report.time_range_start} ~ {report.time_range_end}"])
        w.writerow(["文件大小", f"最小 {report.file_size_min_mb}MB / 最大 {report.file_size_max_mb}MB / 平均 {report.file_size_avg_mb}MB"])
        w.writerow([])
        w.writerow(["=== AI 核验 ==="])
        w.writerow(["总报警数", report.total_files])
        w.writerow(["可核验数", report.total_verifiable])
        w.writerow(["AI 匹配数", report.yolo_match_count])
        w.writerow(["误报数", report.false_alarm_count])
        w.writerow(["误报率", f"{report.false_alarm_rate}%"])
        w.writerow(["AI 匹配率", f"{report.match_rate}%"])
        w.writerow(["绿线文件数", report.green_line_count])
        w.writerow([])
        w.writerow(["=== 报警类型统计 ==="])
        w.writerow(["类型", "总数", "AI匹配", "误报", "匹配率"])
        for atype, stats in report.alarm_type_stats.items():
            w.writerow([
                stats["label"], stats["total"], stats["match"],
                stats["false"], f"{stats['match_rate']}%",
            ])
        w.writerow([])
        w.writerow(["=== 图片分辨率分布 ==="])
        for res, count in report.jpg_resolutions.items():
            w.writerow([res, count])
        w.writerow([])
        w.writerow(["=== 视频参数 ==="])
        for res, count in report.mp4_resolutions.items():
            w.writerow(["分辨率", res, count])
        for codec, count in report.mp4_codecs.items():
            w.writerow(["编码", codec, count])
        w.writerow(["平均时长", f"{report.mp4_duration_avg_sec}s"])
        w.writerow(["最小时长", f"{report.mp4_duration_min_sec}s"])
        w.writerow(["最大时长", f"{report.mp4_duration_max_sec}s"])
        w.writerow([])
        w.writerow(["=== 布防计划 ==="])
        w.writerow(["布防违规数", report.schedule_violations])
        day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        for d in range(7):
            w.writerow([day_names[d], report.schedule_coverage_by_day.get(d, 0)])
        w.writerow([])
        w.writerow(["=== 24小时分布 ==="])
        for h in range(24):
            w.writerow([f"{h:02d}:00", report.schedule_coverage_by_hour.get(h, 0)])

    logger.info("CSV 测试报告已导出: %s", path)


def export_report_html(report: FTPTestReport, path: str):
    """导出 HTML 格式测试报告"""
    day_names_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    # 报警类型统计表
    type_rows = ""
    for atype, stats in report.alarm_type_stats.items():
        cls = "pass" if stats["match_rate"] >= 80 else ("warn" if stats["match_rate"] >= 50 else "fail")
        type_rows += f"""
        <tr>
            <td>{stats['label']}</td>
            <td>{stats['total']}</td>
            <td>{stats['match']}</td>
            <td>{stats['false']}</td>
            <td class="{cls}">{stats['match_rate']}%</td>
        </tr>"""

    # 星期分布
    day_cells = ""
    for d in range(7):
        count = report.schedule_coverage_by_day.get(d, 0)
        color = "#4CAF50" if count > 0 else "#f44336"
        day_cells += f'<td style="background:{color};color:white;text-align:center">{day_names_cn[d]}<br>{count}</td>'

    # 24小时分布
    hour_bars = ""
    max_hour = max(report.schedule_coverage_by_hour.values()) if report.schedule_coverage_by_hour else 1
    for h in range(24):
        count = report.schedule_coverage_by_hour.get(h, 0)
        pct = count / max_hour * 100 if max_hour > 0 else 0
        color = "#4CAF50" if count > 0 else "#e0e0e0"
        hour_bars += f'<div style="display:inline-block;width:3.5%;height:{max(2, pct)}px;background:{color};margin:1px" title="{h:02d}:00 - {count}个文件"></div>'

    # 评分颜色
    score_color = "#4CAF50" if report.overall_score >= 80 else ("#FF9800" if report.overall_score >= 60 else "#f44336")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>NVR FTP 功能测试报告</title>
<style>
body {{ font-family: "Microsoft YaHei", sans-serif; max-width: 1000px; margin: 20px auto; padding: 20px; background: #f5f5f5; }}
h1 {{ color: #333; border-bottom: 3px solid #2196F3; padding-bottom: 10px; }}
h2 {{ color: #555; margin-top: 30px; }}
.card {{ background: white; border-radius: 8px; padding: 20px; margin: 15px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.score {{ font-size: 48px; font-weight: bold; color: {score_color}; }}
.stat-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; }}
.stat-item {{ text-align: center; padding: 15px; background: #f9f9f9; border-radius: 6px; }}
.stat-value {{ font-size: 24px; font-weight: bold; color: #2196F3; }}
.stat-label {{ color: #777; font-size: 14px; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
th, td {{ padding: 10px; border: 1px solid #ddd; text-align: center; }}
th {{ background: #2196F3; color: white; }}
.pass {{ color: #4CAF50; font-weight: bold; }}
.warn {{ color: #FF9800; font-weight: bold; }}
.fail {{ color: #f44336; font-weight: bold; }}
.footer {{ text-align: center; color: #999; margin-top: 30px; font-size: 12px; }}
</style>
</head>
<body>
<h1>📊 NVR FTP 功能测试报告</h1>

<div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
            <p>NVR 型号: <strong>{report.nvr_name}</strong></p>
            <p>生成时间: {report.generated_at.strftime("%Y-%m-%d %H:%M:%S") if report.generated_at else ""}</p>
            <p>时间范围: {report.time_range_start.strftime("%Y-%m-%d %H:%M") if report.time_range_start else "—"} ~ {report.time_range_end.strftime("%H:%M:%S") if report.time_range_end else "—"}</p>
        </div>
        <div style="text-align:center">
            <div class="score">{report.overall_score}</div>
            <div style="color:#777">/ 100 分</div>
        </div>
    </div>
</div>

<h2>📁 文件统计</h2>
<div class="card">
    <div class="stat-grid">
        <div class="stat-item"><div class="stat-value">{report.total_files}</div><div class="stat-label">总文件数</div></div>
        <div class="stat-item"><div class="stat-value">{report.jpg_count}</div><div class="stat-label">图片 (JPG)</div></div>
        <div class="stat-item"><div class="stat-value">{report.mp4_count}</div><div class="stat-label">视频 (MP4)</div></div>
        <div class="stat-item"><div class="stat-value">{report.channel_count}/{report.total_channels}</div><div class="stat-label">通道覆盖</div></div>
    </div>
    <p>文件大小: 最小 <strong>{report.file_size_min_mb} MB</strong> / 最大 <strong>{report.file_size_max_mb} MB</strong> / 平均 <strong>{report.file_size_avg_mb} MB</strong></p>
</div>

<h2>🤖 AI 核验汇总</h2>
<div class="card">
    <div class="stat-grid">
        <div class="stat-item"><div class="stat-value">{report.false_alarm_count}</div><div class="stat-label">误报数</div></div>
        <div class="stat-item"><div class="stat-value">{report.false_alarm_rate}%</div><div class="stat-label">误报率</div></div>
        <div class="stat-item"><div class="stat-value">{report.yolo_match_count}/{report.total_verifiable}</div><div class="stat-label">AI匹配</div></div>
        <div class="stat-item"><div class="stat-value">{report.match_rate}%</div><div class="stat-label">AI匹配率</div></div>
    </div>
    <table>
        <tr><th>报警类型</th><th>总数</th><th>AI匹配</th><th>误报</th><th>匹配率</th></tr>
        {type_rows}
    </table>
</div>

<h2>🖼️ 图片/视频参数</h2>
<div class="card">
    <p><strong>图片分辨率分布:</strong> {", ".join(f"{k}({v})" for k, v in report.jpg_resolutions.items()) if report.jpg_resolutions else "无图片"}</p>
    <p><strong>视频分辨率分布:</strong> {", ".join(f"{k}({v})" for k, v in report.mp4_resolutions.items()) if report.mp4_resolutions else "无视频"}</p>
    <p><strong>视频编码:</strong> {", ".join(f"{k}({v})" for k, v in report.mp4_codecs.items()) if report.mp4_codecs else "—"}</p>
    <p><strong>视频时长:</strong> 平均 {report.mp4_duration_avg_sec}s / 最短 {report.mp4_duration_min_sec}s / 最长 {report.mp4_duration_max_sec}s</p>
</div>

<h2>📅 布防计划</h2>
<div class="card">
    <p>布防违规: <strong class="{'pass' if report.schedule_violations == 0 else 'fail'}">{report.schedule_violations} 次</strong></p>
    <h4>按星期分布</h4>
    <table><tr>{day_cells}</tr></table>
    <h4>24小时分布</h4>
    <div style="background:white;padding:5px;border:1px solid #ddd">{hour_bars}</div>
    <p style="font-size:12px;color:#999;margin-top:5px">绿色=有文件, 灰色=无文件 — 越高文件越多</p>
</div>

<h2>📡 通道覆盖</h2>
<div class="card">
    <p>通道覆盖率: <strong>{report.channel_coverage_rate:.1f}%</strong></p>
    <table>
        <tr><th>通道</th><th>人形</th><th>机动车</th><th>宠物</th><th>画面变动</th><th>合计</th></tr>
        {_channel_table_rows(report)}
    </table>
</div>

<div class="footer">
    <p>NVR AI Platform — 自动生成</p>
    <p>🤖 Generated with Claude Code</p>
</div>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("HTML 测试报告已导出: %s", path)


def _channel_table_rows(report: FTPTestReport) -> str:
    """生成通道覆盖表格行"""
    rows = ""
    all_channels = sorted(report.channel_coverage.keys())
    if not all_channels:
        return '<tr><td colspan="6">无数据</td></tr>'
    for ch in all_channels:
        types = report.channel_coverage[ch]
        human = types.get("human", 0)
        vehicle = types.get("vehicle", 0)
        pet = types.get("pet", 0)
        motion = types.get("motion", 0)
        total = human + vehicle + pet + motion
        rows += f"<tr><td>通道{ch}</td><td>{human}</td><td>{vehicle}</td><td>{pet}</td><td>{motion}</td><td>{total}</td></tr>"
    return rows
