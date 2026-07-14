"""
alert_panel.py — 报警核验面板（双 Tab）

Tab 1 — "报警明细": 逐条显示 FTP 文件核验结果（表格 + 颜色标记）
Tab 2 — "FTP 测试报告": 聚合统计报告（仪表盘视图）

支持：
- 行颜色编码（红=误报 / 黄=绿线 / 灰=画面变动 / 绿=正常）
- 双击打开文件
- 导出 CSV
- 实时刷新统计报告
"""

import os
import csv
from datetime import datetime
from typing import List, Dict, Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QTextBrowser, QFileDialog, QSplitter, QMessageBox,
    QGroupBox, QGridLayout, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QBrush

from core.alarm_types import get_friendly_name, get_coco_cn_name
from core.ftp_test_reporter import FTPTestReporter, FTPTestReport, export_report_csv, export_report_html
from utils.logger import get_logger

logger = get_logger("alert_panel")


# ═══════════════════════════════════════════════════════════
# 报警明细 Tab
# ═══════════════════════════════════════════════════════════

class AlarmDetailTab(QWidget):
    """报警明细表格"""

    alert_double_clicked = pyqtSignal(dict)  # 双击某行时发出完整数据
    export_requested = pyqtSignal()

    COLUMNS = [
        "时间", "通道", "文件名", "类型", "AI检测结果", "置信度",
        "误报", "绿线", "文件参数", "配置",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._alerts: List[Dict] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 工具栏 ──────────────────────────────────
        toolbar = QHBoxLayout()
        self._summary_label = QLabel("共 0 条报警 | 误报: 0 | 正常: 0")
        toolbar.addWidget(self._summary_label)
        toolbar.addStretch()

        self._btn_export_csv = QPushButton("📥 导出明细 CSV")
        self._btn_export_csv.clicked.connect(self._on_export_csv)
        toolbar.addWidget(self._btn_export_csv)

        self._btn_clear = QPushButton("🗑 清空")
        self._btn_clear.clicked.connect(self.clear)
        toolbar.addWidget(self._btn_clear)
        layout.addLayout(toolbar)

        # ── 表格 ────────────────────────────────────
        self._table = QTableWidget(0, len(self.COLUMNS), self)
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.cellDoubleClicked.connect(self._on_double_click)
        self._table.setSortingEnabled(True)
        layout.addWidget(self._table)

    # ── 公共方法 ────────────────────────────────────────

    def add_alert(self, r: Dict):
        """添加或更新一条报警记录（by filename 去重）"""
        # 检查是否已存在同名
        filename = r.get("filename", "")
        for i, existing in enumerate(self._alerts):
            if existing.get("filename") == filename:
                self._alerts[i] = r
                self._refresh_row(i, r)
                self._refresh_summary()
                return

        # 新增
        self._alerts.insert(0, r)
        self._insert_row(0, r)
        self._refresh_summary()

    def update_alert(self, filename: str, partial: Dict):
        """更新已存在的行（用于从"处理中"更新为完整结果）"""
        for i, existing in enumerate(self._alerts):
            if existing.get("filename") == filename:
                existing.update(partial)
                self._refresh_row(i, existing)
                self._refresh_summary()
                return

    def clear(self):
        """清空全部"""
        self._alerts.clear()
        self._table.setRowCount(0)
        self._refresh_summary()

    def get_all_alerts(self) -> List[Dict]:
        """返回所有报警数据"""
        return list(self._alerts)

    def row_count(self) -> int:
        return len(self._alerts)

    # ── 内部方法 ────────────────────────────────────────

    def _insert_row(self, row_idx: int, r: Dict):
        """在指定位置插入一行"""
        self._table.insertRow(row_idx)
        self._fill_row(row_idx, r)

    def _refresh_row(self, row_idx: int, r: Dict):
        """刷新指定行"""
        self._fill_row(row_idx, r)

    def _fill_row(self, row: int, r: Dict):
        """填充一行的所有列"""
        # 0. 时间
        ts = r.get("alarm_timestamp", "")
        if isinstance(ts, datetime):
            ts = ts.strftime("%m-%d %H:%M:%S")
        self._set_cell(row, 0, ts)

        # 1. 通道
        ch = r.get("channel", "?")
        self._set_cell(row, 1, str(ch))

        # 2. 文件名
        self._set_cell(row, 2, r.get("filename", "?"))

        # 3. NVR 报警类型
        nvr_type = r.get("nvr_alarm_label", r.get("nvr_alarm_type", "?"))
        self._set_cell(row, 3, nvr_type)

        # 4. AI 检测结果
        yolo_result = self._format_yolo_result(r)
        self._set_cell(row, 4, yolo_result)

        # 5. 置信度
        conf = r.get("yolo_max_confidence", 0)
        conf_text = f"{conf:.2f}" if conf else "—"
        self._set_cell(row, 5, conf_text)

        # 6. 误报判定
        is_false = r.get("is_false_alarm", False)
        yolo_applicable = r.get("yolo_applicable", False)
        if is_false:
            self._set_cell(row, 6, "❌ 误报", QColor(255, 80, 80))
        elif yolo_applicable:
            self._set_cell(row, 6, "✅ 正常", QColor(80, 180, 80))
        else:
            self._set_cell(row, 6, "—")

        # 7. 绿线检测
        green = r.get("green_line_detected", False)
        if green:
            self._set_cell(row, 7, "⚠️ 有绿线", QColor(255, 180, 50))
        else:
            self._set_cell(row, 7, "✅ 正常", QColor(80, 180, 80))

        # 8. 文件参数
        params = self._format_file_params(r)
        self._set_cell(row, 8, params)

        # 9. 配置校验
        config_all = r.get("config_all_pass")
        if config_all is True:
            self._set_cell(row, 9, "✅ 全部通过")
        elif config_all is False:
            # 统计失败项
            checks = r.get("config_checks", [])
            fails = [c["check_name"] for c in checks if not c.get("passed")]
            self._set_cell(row, 9, f"❌ {', '.join(fails)}", QColor(255, 80, 80))
        else:
            self._set_cell(row, 9, "—")

        # 行颜色
        self._apply_row_color(row, r)

    def _set_cell(self, row: int, col: int, text: str, fg_color: Optional[QColor] = None):
        """设置单元格文本和前景色"""
        item = QTableWidgetItem(text)
        item.setToolTip(text)  # tooltip 显示完整内容
        if fg_color:
            item.setForeground(QBrush(fg_color))
        self._table.setItem(row, col, item)

    def _apply_row_color(self, row: int, r: Dict):
        """根据核验结果设置行背景色"""
        is_false = r.get("is_false_alarm", False)
        green_line = r.get("green_line_detected", False)
        file_type = r.get("file_type", "")
        yolo_applicable = r.get("yolo_applicable", False)

        if is_false:
            color = QColor(255, 230, 230)  # 浅红 — 误报
        elif green_line:
            color = QColor(255, 250, 210)  # 浅黄 — 绿线
        elif not yolo_applicable and file_type:
            color = QColor(240, 240, 240)  # 浅灰 — 画面变动(无YOLO)
        else:
            color = QColor(230, 255, 230)  # 浅绿 — 正常

        for col in range(len(self.COLUMNS)):
            item = self._table.item(row, col)
            if item:
                item.setBackground(QBrush(color))

    def _format_yolo_result(self, r: Dict) -> str:
        """格式化 YOLO 检测结果显示"""
        if not r.get("yolo_applicable"):
            return "— (无需YOLO)"

        classes = r.get("yolo_classes_found", [])
        per_class = r.get("yolo_detections_per_class", {})
        if not classes:
            return "❌ 无目标"

        parts = []
        for cls_name, count in per_class.items():
            parts.append(f"{cls_name}({count})")
        return ", ".join(parts)

    def _format_file_params(self, r: Dict) -> str:
        """格式化文件参数显示"""
        file_type = r.get("file_type", "")
        parts = []

        if file_type == "image":
            w = r.get("image_width")
            h = r.get("image_height")
            if w and h:
                parts.append(f"{w}×{h}")
        elif file_type == "video":
            w = r.get("video_width")
            h = r.get("video_height")
            if w and h:
                parts.append(f"{w}×{h}")
            dur = r.get("video_duration_sec")
            if dur:
                parts.append(f"{dur}s")
            codec = r.get("video_codec")
            if codec:
                parts.append(codec)

        size = r.get("file_size_mb", 0)
        if size:
            parts.append(f"{size:.1f}MB")

        return " | ".join(parts) if parts else "—"

    def _refresh_summary(self):
        """刷新统计摘要"""
        total = len(self._alerts)
        false_count = sum(1 for a in self._alerts if a.get("is_false_alarm"))
        normal = total - false_count
        self._summary_label.setText(
            f"共 {total} 条报警 | 误报: {false_count} | 正常: {normal}"
        )

    def _on_double_click(self, row: int, _col: int):
        """双击行 → 打开文件"""
        filepath = self._alerts[row].get("file_path", "")
        if filepath and os.path.exists(filepath):
            self._open_file(filepath)
        self.alert_double_clicked.emit(self._alerts[row])

    @staticmethod
    def _open_file(filepath: str):
        """跨平台用系统默认程序打开文件"""
        import platform
        import subprocess as sp
        system = platform.system()
        try:
            if system == "Windows":
                import os as _os
                _os.startfile(filepath)
            elif system == "Darwin":
                sp.Popen(["open", filepath])
            else:
                sp.Popen(["xdg-open", filepath])
        except Exception:
            # 最后的 fallback
            try:
                sp.Popen(["xdg-open", filepath])
            except Exception:
                pass

    def _on_export_csv(self):
        """导出明细 CSV"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出报警明细", "alarm_details.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(self.COLUMNS)
            for r in self._alerts:
                w.writerow([
                    r.get("alarm_timestamp", ""),
                    r.get("channel", ""),
                    r.get("filename", ""),
                    r.get("nvr_alarm_label", ""),
                    self._format_yolo_result(r),
                    r.get("yolo_max_confidence", ""),
                    "误报" if r.get("is_false_alarm") else "正常",
                    "有绿线" if r.get("green_line_detected") else "正常",
                    self._format_file_params(r),
                    "通过" if r.get("config_all_pass") else ("失败" if r.get("config_all_pass") is False else "—"),
                ])
        logger.info("报警明细已导出: %s", path)


# ═══════════════════════════════════════════════════════════
# FTP 测试报告 Tab
# ═══════════════════════════════════════════════════════════

class FTPReportTab(QWidget):
    """FTP 测试报告仪表盘"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._reporter = FTPTestReporter(total_channels=16)
        self._report: Optional[FTPTestReport] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # ── 工具栏 ──────────────────────────────────
        toolbar = QHBoxLayout()
        self._title_label = QLabel("📊 NVR FTP 功能测试报告")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        self._title_label.setFont(font)
        toolbar.addWidget(self._title_label)
        toolbar.addStretch()

        self._btn_refresh = QPushButton("🔄 刷新报告")
        self._btn_refresh.clicked.connect(self.refresh)
        toolbar.addWidget(self._btn_refresh)

        self._btn_export_html = QPushButton("📄 导出 HTML 报告")
        self._btn_export_html.clicked.connect(self._export_html)
        toolbar.addWidget(self._btn_export_html)

        self._btn_export_csv = QPushButton("📥 导出 CSV 报告")
        self._btn_export_csv.clicked.connect(self._export_csv)
        toolbar.addWidget(self._btn_export_csv)
        layout.addLayout(toolbar)

        # ── 主体：使用 QTextBrowser 展示富文本 ────────
        self._browser = QTextBrowser(self)
        self._browser.setOpenExternalLinks(True)
        layout.addWidget(self._browser)

        # 初始提示
        self._browser.setHtml(
            '<p style="color:#999;text-align:center;padding:50px">'
            '暂无测试数据<br>请启动 FTP 监控以收集报警文件'
            '</p>'
        )

    # ── 公共方法 ────────────────────────────────────────

    def set_total_channels(self, n: int):
        old_count = self._reporter.result_count if self._reporter else 0
        self._reporter = FTPTestReporter(total_channels=n)
        if old_count > 0:
            logger.warning(
                "FTPReportTab.set_total_channels() 重建了报告聚合器，"
                "之前 %d 条数据已丢失。请在添加数据前调用此方法。", old_count
            )

    def add_result(self, r: Dict):
        """添加一条核验结果"""
        self._reporter.add_result(r)

    def refresh(self):
        """重新生成报告"""
        if self._reporter.result_count == 0:
            self._browser.setHtml(
                '<p style="color:#999;text-align:center;padding:50px">'
                '暂无测试数据'
                '</p>'
            )
            return

        self._report = self._reporter.generate()
        self._render_report()

    def _render_report(self):
        """渲染报告到 QTextBrowser"""
        r = self._report
        if not r:
            return

        score_color = (
            "#4CAF50" if r.overall_score >= 80
            else "#FF9800" if r.overall_score >= 60
            else "#f44336"
        )

        # 报警类型统计
        type_rows = ""
        for atype, stats in r.alarm_type_stats.items():
            cls = "pass" if stats["match_rate"] >= 80 else ("warn" if stats["match_rate"] >= 50 else "fail")
            color = "#4CAF50" if cls == "pass" else "#FF9800" if cls == "warn" else "#f44336"
            type_rows += f"""
            <tr>
                <td>{stats['label']}</td>
                <td>{stats['total']}</td>
                <td>{stats['match']}</td>
                <td>{stats['false']}</td>
                <td style="color:{color};font-weight:bold">{stats['match_rate']}%</td>
            </tr>"""

        # 通道覆盖
        channel_rows = ""
        for ch in sorted(r.channel_coverage.keys()):
            types = r.channel_coverage[ch]
            channel_rows += f"<tr><td>通道{ch}</td><td>{types.get('human',0)}</td><td>{types.get('vehicle',0)}</td><td>{types.get('pet',0)}</td><td>{types.get('motion',0)}</td></tr>"

        # 分辨率
        jpg_res_text = ", ".join(f"{k}({v})" for k, v in r.jpg_resolutions.items()) or "—"
        mp4_res_text = ", ".join(f"{k}({v})" for k, v in r.mp4_resolutions.items()) or "—"
        mp4_codec_text = ", ".join(f"{k}({v})" for k, v in r.mp4_codecs.items()) or "—"

        # 评分明细
        score_details = "".join(f"<li>{d}</li>" for d in r.score_details)

        html = f"""
<style>
body {{ font-family: "Microsoft YaHei", sans-serif; }}
h2 {{ color: #333; margin-top: 25px; }}
.card {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; padding: 15px; margin: 10px 0; }}
.stat-grid {{ display: flex; gap: 15px; flex-wrap: wrap; }}
.stat-item {{ flex: 1; min-width: 100px; text-align: center; padding: 12px; background: #f9f9f9; border-radius: 6px; }}
.stat-value {{ font-size: 22px; font-weight: bold; color: #2196F3; }}
.stat-label {{ color: #777; font-size: 13px; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
th, td {{ padding: 8px; border: 1px solid #ddd; text-align: center; }}
th {{ background: #2196F3; color: white; }}
.score-big {{ font-size: 56px; font-weight: bold; color: {score_color}; }}
</style>

<h3>基础统计</h3>
<div class="card">
    <div class="stat-grid">
        <div class="stat-item"><div class="stat-value">{r.total_files}</div><div class="stat-label">总文件数</div></div>
        <div class="stat-item"><div class="stat-value">{r.jpg_count}</div><div class="stat-label">图片</div></div>
        <div class="stat-item"><div class="stat-value">{r.mp4_count}</div><div class="stat-label">视频</div></div>
        <div class="stat-item"><div class="stat-value">{r.channel_count}/{r.total_channels}</div><div class="stat-label">覆盖通道</div></div>
        <div class="stat-item"><div class="stat-value" style="color:{score_color}">{r.overall_score}</div><div class="stat-label">综合评分/100</div></div>
    </div>
    <p>文件大小: 最小 <b>{r.file_size_min_mb} MB</b> / 最大 <b>{r.file_size_max_mb} MB</b> / 平均 <b>{r.file_size_avg_mb} MB</b>
    | 时间范围: {r.time_range_start.strftime('%m-%d %H:%M') if r.time_range_start else '—'} ~ {r.time_range_end.strftime('%H:%M') if r.time_range_end else '—'}</p>
</div>

<h3>评分明细</h3>
<div class="card"><ul>{score_details}</ul></div>

<h3>AI 核验汇总</h3>
<div class="card">
    <div class="stat-grid">
        <div class="stat-item"><div class="stat-value" style="color:#f44336">{r.false_alarm_count}</div><div class="stat-label">误报数</div></div>
        <div class="stat-item"><div class="stat-value" style="color:#f44336">{r.false_alarm_rate}%</div><div class="stat-label">误报率</div></div>
        <div class="stat-item"><div class="stat-value" style="color:#4CAF50">{r.yolo_match_count}/{r.total_verifiable}</div><div class="stat-label">AI匹配</div></div>
        <div class="stat-item"><div class="stat-value" style="color:#4CAF50">{r.match_rate}%</div><div class="stat-label">AI匹配率</div></div>
        <div class="stat-item"><div class="stat-value" style="color:#FF9800">{r.green_line_count}</div><div class="stat-label">绿线文件</div></div>
    </div>
    <table>
        <tr><th>报警类型</th><th>总数</th><th>AI匹配</th><th>误报</th><th>匹配率</th></tr>
        {type_rows}
    </table>
</div>

<h3>图片/视频参数</h3>
<div class="card">
    <p><b>图片分辨率:</b> {jpg_res_text}</p>
    <p><b>视频分辨率:</b> {mp4_res_text}</p>
    <p><b>视频编码:</b> {mp4_codec_text}</p>
    <p><b>视频时长:</b> 平均 {r.mp4_duration_avg_sec}s / 最短 {r.mp4_duration_min_sec}s / 最长 {r.mp4_duration_max_sec}s</p>
    <p><b>图片上传间隔:</b> 平均 {r.jpg_upload_interval_avg_sec}s</p>
</div>

<h3>通道覆盖</h3>
<div class="card">
    <p>通道覆盖率: <b>{r.channel_coverage_rate:.1f}%</b></p>
    <table>
        <tr><th>通道</th><th>人形</th><th>机动车</th><th>宠物</th><th>画面变动</th></tr>
        {channel_rows}
    </table>
</div>
"""
        self._browser.setHtml(html)

    def _export_html(self):
        """导出 HTML 报告"""
        if not self._report:
            QMessageBox.information(self, "提示", "暂无数据，请先收集报警文件。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 HTML 测试报告", "ftp_test_report.html", "HTML Files (*.html)"
        )
        if path:
            export_report_html(self._report, path)
            QMessageBox.information(self, "完成", f"HTML 报告已导出:\n{path}")

    def _export_csv(self):
        """导出 CSV 报告"""
        if not self._report:
            QMessageBox.information(self, "提示", "暂无数据，请先收集报警文件。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV 测试报告", "ftp_test_report.csv", "CSV Files (*.csv)"
        )
        if path:
            export_report_csv(self._report, path)
            QMessageBox.information(self, "完成", f"CSV 报告已导出:\n{path}")


# ═══════════════════════════════════════════════════════════
# AlertPanel（双 Tab 总控）
# ═══════════════════════════════════════════════════════════

class AlertPanel(QWidget):
    """
    报警核验面板。

    Tab 1: 报警明细（逐条核验结果表格）
    Tab 2: FTP 测试报告（聚合统计仪表盘）

    兼容旧版 add_alert(text) 方法。
    """

    alert_selected = pyqtSignal(dict)
    alert_double_clicked = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._detail_tab: Optional[AlarmDetailTab] = None
        self._report_tab: Optional[FTPReportTab] = None
        self._tab_widget: Optional[QTabWidget] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._tab_widget = QTabWidget(self)

        # Tab 1: 报警明细
        self._detail_tab = AlarmDetailTab(self)
        self._detail_tab.alert_double_clicked.connect(self.alert_double_clicked.emit)
        self._tab_widget.addTab(self._detail_tab, "📋 报警明细")

        # Tab 2: FTP 测试报告
        self._report_tab = FTPReportTab(self)
        self._tab_widget.addTab(self._report_tab, "📊 FTP 测试报告")

        layout.addWidget(self._tab_widget)

    # ── 公共接口 ────────────────────────────────────────

    def add_alert(self, record_or_text):
        """
        添加报警。

        兼容两种调用:
        - add_alert(dict):  新的结构化 FTP 报警记录
        - add_alert(str):   旧版简单文本（向后兼容）
        """
        if isinstance(record_or_text, str):
            # 旧版兼容：简单文本记录
            self._detail_tab.add_alert({
                "alarm_timestamp": datetime.now().strftime("%m-%d %H:%M:%S"),
                "channel": "—",
                "filename": record_or_text[:50],
                "nvr_alarm_label": "文本告警",
                "file_type": "",
            })
            return

        # 新版：结构化记录
        self._detail_tab.add_alert(record_or_text)

        # 同时加入报告聚合器
        if self._report_tab:
            self._report_tab.add_result(record_or_text)

    def update_alert(self, filename: str, partial: Dict):
        """更新已存在的报警行"""
        self._detail_tab.update_alert(filename, partial)
        # ★ 同步更新报告聚合器（用完整核验结果替换旧的 pending 记录）
        if self._report_tab:
            self._report_tab.add_result(partial)

    def add_pending(self, record: Dict):
        """
        添加一条"处理中"记录（FTP 文件刚到达，还未核验）。
        核验完成后用 update_alert() 更新。
        """
        filename = record.get("original", record.get("filename", ""))
        alarm_type = record.get("alarm_type", "?")
        label = get_friendly_name(alarm_type)

        pending_record = {
            "alarm_timestamp": record.get("timestamp", datetime.now()),
            "channel": record.get("channel", "?"),
            "filename": filename,
            "nvr_alarm_type": alarm_type,
            "nvr_alarm_label": label,
            "file_type": record.get("file_type", ""),
        }
        self._detail_tab.add_alert(pending_record)
        # ★ 同时加入报告聚合器（修复：之前漏掉了）
        if self._report_tab:
            self._report_tab.add_result(pending_record)

    def refresh_report(self):
        """刷新测试报告 Tab"""
        if self._report_tab:
            self._report_tab.refresh()

    def set_total_channels(self, n: int):
        """设置 NVR 总通道数"""
        if self._report_tab:
            self._report_tab.set_total_channels(n)

    def clear(self):
        """清空所有数据和报告"""
        self._detail_tab.clear()
        # 重置报告聚合器
        if self._report_tab:
            self._report_tab._reporter.clear()
            self._report_tab._browser.setHtml(
                '<p style="color:#999;text-align:center;padding:50px">'
                '已清空<br>请重新收集报警文件'
                '</p>'
            )

    def get_all_alerts(self) -> List[Dict]:
        return self._detail_tab.get_all_alerts()

    @property
    def detail_tab(self) -> AlarmDetailTab:
        return self._detail_tab

    @property
    def report_tab(self) -> FTPReportTab:
        return self._report_tab
