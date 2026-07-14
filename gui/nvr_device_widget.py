"""
nvr_device_widget.py — NVR 设备状态面板

基于 reolink_aio SDK 的 NVR 设备信息面板。
显示：型号、固件、通道数、AI检测状态、端口状态等。
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QGridLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QProgressBar, QTextEdit,
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QBrush

from core.reolink_device import ReolinkDevice, ReolinkDeviceInfo, ReolinkAIState
from config import settings
from utils.logger import get_logger

logger = get_logger("nvr_device_widget")


class NvrDeviceWidget(QWidget):
    """NVR 设备状态面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._device: ReolinkDevice = None
        self._info: ReolinkDeviceInfo = None
        self._connected = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # 标题
        title = QLabel("NVR 设备状态 (reolink_aio API)")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        layout.addWidget(title)

        # 连接控制
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("NVR IP:"))
        self._host_input = QLineEdit(settings.NVR_HOST if hasattr(settings, 'NVR_HOST') else "192.168.124.2")
        ctrl.addWidget(self._host_input, stretch=1)
        ctrl.addWidget(QLabel("用户:"))
        self._user_input = QLineEdit("admin")
        self._user_input.setMaximumWidth(80)
        ctrl.addWidget(self._user_input)
        ctrl.addWidget(QLabel("密码:"))
        self._pwd_input = QLineEdit("111111..")
        self._pwd_input.setEchoMode(QLineEdit.Password)
        self._pwd_input.setMaximumWidth(80)
        ctrl.addWidget(self._pwd_input)

        self._btn_connect = QPushButton("连接 NVR")
        self._btn_connect.clicked.connect(self._on_connect)
        ctrl.addWidget(self._btn_connect)

        self._btn_disconnect = QPushButton("断开")
        self._btn_disconnect.setEnabled(False)
        self._btn_disconnect.clicked.connect(self._on_disconnect)
        ctrl.addWidget(self._btn_disconnect)

        layout.addLayout(ctrl)

        # 进度条
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # 设备信息组
        self._info_group = QGroupBox("设备信息")
        self._info_grid = QGridLayout(self._info_group)
        self._info_grid.setSpacing(4)

        self._info_labels: dict[str, QLabel] = {}
        rows = [
            ("model", "型号"), ("model_number", "型号代码"), ("firmware", "固件版本"),
            ("hardware", "硬件版本"), ("mac", "MAC 地址"), ("manufacturer", "制造商"),
            ("channels", "总通道数"), ("cameras", "已接入摄像头"), ("hdd", "硬盘容量"),
            ("rtsp", "RTSP"), ("onvif", "ONVIF"), ("rtmp", "RTMP"),
            ("user", "用户等级"), ("serial", "序列号"),
        ]
        for i, (key, label) in enumerate(rows):
            lbl = QLabel(label + ":")
            lbl.setStyleSheet("color: #555; font-size: 12px;")
            val = QLabel("—")
            val.setStyleSheet("font-weight: bold; font-size: 12px; color: #333;")
            self._info_grid.addWidget(lbl, i // 2, (i % 2) * 2)
            self._info_grid.addWidget(val, i // 2, (i % 2) * 2 + 1)
            self._info_labels[key] = val

        layout.addWidget(self._info_group)

        # AI 检测状态表
        ai_group = QGroupBox("AI 检测状态 (按通道)")
        ai_layout = QVBoxLayout(ai_group)

        self._ai_table = QTableWidget(0, 5)
        self._ai_table.setHorizontalHeaderLabels(["通道", "AI 启用", "人形", "车辆", "宠物"])
        self._ai_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._ai_table.setMaximumHeight(300)
        ai_layout.addWidget(self._ai_table)

        layout.addWidget(ai_group)

    # ── 连接管理 ──────────────────────────────────────

    def _on_connect(self):
        host = self._host_input.text().strip()
        user = self._user_input.text().strip()
        pwd = self._pwd_input.text().strip()

        if not host:
            QMessageBox.warning(self, "错误", "请输入 NVR IP 地址")
            return

        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._btn_connect.setEnabled(False)

        # 后台线程连接（不阻塞 UI）
        self._device = ReolinkDevice(host, user, pwd)

        class ConnectThread(QThread):
            result = pyqtSignal(object)

            def __init__(self, device):
                super().__init__()
                self._device = device

            def run(self):
                info = self._device.connect()
                self.result.emit(info)

        self._connect_thread = ConnectThread(self._device)
        self._connect_thread.result.connect(self._on_connect_result)
        self._connect_thread.start()

    def _on_connect_result(self, info: ReolinkDeviceInfo):
        self._progress.setVisible(False)
        self._btn_connect.setEnabled(True)

        self._info = info
        self._connected = info.connected

        if info.connected:
            self._btn_disconnect.setEnabled(True)
            self._display_info(info)
            self._refresh_ai_states()
        else:
            QMessageBox.warning(self, "连接失败", "无法连接到 NVR。请检查 IP/用户名/密码。")

    def _on_disconnect(self):
        if self._device:
            self._device.disconnect()
        self._connected = False
        self._btn_connect.setEnabled(True)
        self._btn_disconnect.setEnabled(False)

        # 清空显示
        for v in self._info_labels.values():
            v.setText("—")

        self._ai_table.setRowCount(0)

    def _display_info(self, info: ReolinkDeviceInfo):
        """显示设备信息"""
        self._info_labels["model"].setText(info.model_name)
        self._info_labels["model_number"].setText(info.model_number)
        self._info_labels["firmware"].setText(info.firmware_version)
        self._info_labels["hardware"].setText(info.hardware_version)
        self._info_labels["mac"].setText(info.mac_address)
        self._info_labels["manufacturer"].setText(info.manufacturer)
        self._info_labels["channels"].setText(f"{info.num_channels} (实际摄像头: {info.num_cameras})")
        self._info_labels["cameras"].setText(str(info.num_cameras))
        self._info_labels["hdd"].setText(f"{info.hdd_count} 块硬盘 / 共 {info.hdd_total_gb:.0f} GB")
        self._info_labels["rtsp"].setText(f"{'✅' if info.rtsp_enabled else '❌'} 端口 {info.rtsp_port}")
        self._info_labels["onvif"].setText(f"{'✅' if info.onvif_enabled else '❌'} 端口 {info.onvif_port}")
        self._info_labels["rtmp"].setText(f"{'✅' if info.rtmp_enabled else '❌'} 端口 {info.rtmp_port}")
        self._info_labels["user"].setText(f"{info.user_level} {'(管理员)' if info.is_admin else ''}")

        serial = info.serial if info.serial and info.serial != 'Unknown' else '—'
        self._info_labels["serial"].setText(serial)

    def _refresh_ai_states(self):
        """刷新 AI 状态表"""
        if not self._connected or not self._device:
            return

        states = self._device.get_ai_states()
        self._ai_table.setRowCount(0)

        active_count = 0
        for s in states:
            row = self._ai_table.rowCount()
            self._ai_table.insertRow(row)

            self._set_cell(row, 0, f"Ch{s.channel:02d}")

            if s.ai_enabled:
                self._ai_table.item(row, 0).setBackground(QBrush(QColor(200, 255, 200)))
                active_count += 1

            self._set_cell(row, 1, "是" if s.ai_enabled else "否")
            self._set_cell(row, 2, "✅" if s.person_detected else "—")
            self._set_cell(row, 3, "✅" if s.vehicle_detected else "—")
            self._set_cell(row, 4, "✅" if s.pet_detected else "—")

        if active_count == 0 and len(states) > 0:
            # 加一行提示
            row = self._ai_table.rowCount()
            self._ai_table.insertRow(row)
            item = QTableWidgetItem(f"当前无 AI 检测事件（共 {len(states)} 路通道已查询）")
            item.setForeground(QBrush(QColor("#999")))
            self._ai_table.setItem(row, 0, item)

    def _set_cell(self, row: int, col: int, text: str):
        item = QTableWidgetItem(text)
        self._ai_table.setItem(row, col, item)
