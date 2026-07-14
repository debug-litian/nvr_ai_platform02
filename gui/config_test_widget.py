"""
config_test_widget.py — NVR 布防配置测试 GUI 面板

对接 NvrConfigTester，对 FTP 核验结果运行 9 大类配置测试，
展示测试报告表格。
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QProgressBar, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QScrollArea, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor, QBrush

from core.nvr_config_tester import NvrConfigTester, ConfigTestReport
from config import settings
from utils.logger import get_logger

logger = get_logger("config_test_widget")


class ConfigTestWidget(QWidget):
    """NVR 布防配置测试面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tester = NvrConfigTester(str(settings.NVR_PROFILE_PATH))
        self._report: ConfigTestReport = None
        self._results: list = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # 标题
        title = QLabel("NVR 布防配置测试")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        layout.addWidget(title)

        desc = QLabel(
            "基于 nvr_profile.json 对 FTP 报警数据进行 9 大类配置测试：\n"
            "布防计划 / 报警类型 / 灵敏度 / 报警联动 / 录像参数 / "
            "区域设置 / 目标过滤 / FTP设置 / 邮件设置"
        )
        desc.setStyleSheet("color: #777; font-size: 11px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 控制栏
        ctrl = QHBoxLayout()

        ctrl.addWidget(QLabel("配置文件:"))
        self._profile_label = QLabel(str(settings.NVR_PROFILE_PATH))
        self._profile_label.setStyleSheet("color: #1565c0; font-size: 11px;")
        ctrl.addWidget(self._profile_label, stretch=1)

        self._btn_run = QPushButton("运行配置测试")
        self._btn_run.clicked.connect(self._on_run_test)
        ctrl.addWidget(self._btn_run)

        self._btn_export = QPushButton("导出 CSV")
        self._btn_export.clicked.connect(self._on_export_csv)
        self._btn_export.setEnabled(False)
        ctrl.addWidget(self._btn_export)

        layout.addLayout(ctrl)

        # 进度条
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # 汇总
        self._summary_label = QLabel("等待运行...")
        self._summary_label.setFont(QFont("Microsoft YaHei", 10))
        self._summary_label.setStyleSheet("padding: 8px;")
        layout.addWidget(self._summary_label)

        # 结果表格
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "大类", "检查项", "通道", "期望值", "实际值", "结果",
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self._table, stretch=1)

    # ── 输入接口 ──────────────────────────────────────

    def set_results(self, results: list):
        """设置待测试的核验结果列表"""
        self._results = results
        self._summary_label.setText(f"已加载 {len(results)} 条核验结果，点击运行测试")

    def add_result(self, result: dict):
        """追加一条"""
        self._results.append(result)

    # ── 动作 ──────────────────────────────────────────

    def _on_run_test(self):
        if not self._results:
            QMessageBox.information(self, "提示", "暂无 FTP 核验数据。请先启动 FTP 监控收集数据。")
            return

        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._btn_run.setEnabled(False)

        # 重新加载配置
        self._tester.load_profile(str(settings.NVR_PROFILE_PATH))

        # 运行测试
        self._report = self._tester.run_all_checks(self._results)

        # 显示
        self._display_report()
        self._progress.setVisible(False)
        self._btn_run.setEnabled(True)
        self._btn_export.setEnabled(True)

    def _display_report(self):
        r = self._report
        if not r:
            return

        # 汇总
        color = "#4CAF50" if r.pass_rate >= 80 else "#FF9800" if r.pass_rate >= 60 else "#f44336"
        self._summary_label.setText(
            f"总检查项: {r.total_checks} | "
            f"<span style='color:#4CAF50'>通过: {r.passed}</span> | "
            f"<span style='color:#f44336'>失败: {r.failed}</span> | "
            f"跳过: {r.skipped} | "
            f"通过率: <span style='color:{color};font-weight:bold'>{r.pass_rate:.1f}%</span>"
        )
        self._summary_label.setTextFormat(Qt.RichText)

        # 按分类汇总
        cat_text = ""
        for cat, s in r.categories.items():
            short = cat[:20]
            c = "#4CAF50" if s.pass_rate >= 80 else "#FF9800" if s.pass_rate >= 60 else "#f44336"
            cat_text += f"<span style='margin:0 8px'>{short}: <b style='color:{c}'>{s.pass_rate:.0f}%</b> ({s.passed}/{s.total})</span>"
        self._summary_label.setText(self._summary_label.text() + "<br>" + cat_text)

        # 表格
        self._table.setRowCount(0)
        for item in r.items:
            row = self._table.rowCount()
            self._table.insertRow(row)

            self._set_cell(row, 0, item.category[:25])

            name = item.check_name
            if item.channel >= 0:
                name = f"[Ch{item.channel}] {name}"
            self._set_cell(row, 1, name)

            self._set_cell(row, 2, str(item.channel) if item.channel >= 0 else "全局")
            self._set_cell(row, 3, item.expected)
            self._set_cell(row, 4, item.actual)

            result_cell = QTableWidgetItem("PASS" if item.passed else "FAIL")
            if item.passed:
                result_cell.setForeground(QBrush(QColor("#4CAF50")))
                result_cell.setFont(QFont("Consolas", 9, QFont.Bold))
            else:
                result_cell.setForeground(QBrush(QColor("#f44336")))
                result_cell.setFont(QFont("Consolas", 9, QFont.Bold))
            result_cell.setToolTip(item.detail)
            self._table.setItem(row, 5, result_cell)

            # 行颜色
            if not item.passed:
                for col in range(6):
                    w = self._table.item(row, col)
                    if w:
                        w.setBackground(QBrush(QColor(255, 230, 230)))

    def _set_cell(self, row: int, col: int, text: str):
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        self._table.setItem(row, col, item)

    def _on_export_csv(self):
        import csv
        from PyQt5.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self, "导出配置测试报告", "nvr_config_test.csv", "CSV Files (*.csv)"
        )
        if not path:
            return

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["大类", "检查项", "通道", "期望值", "实际值", "结果", "详情"])
            for item in self._report.items:
                w.writerow([
                    item.category, item.check_name,
                    item.channel if item.channel >= 0 else "全局",
                    item.expected, item.actual,
                    "PASS" if item.passed else "FAIL",
                    item.detail,
                ])

        QMessageBox.information(self, "完成", f"配置测试报告已导出:\n{path}")
