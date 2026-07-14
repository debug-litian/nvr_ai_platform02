"""
navigation_bar.py — 仿 NVR 风格的顶部导航栏

样式特点：
- 深色背景 (#16213e)，类似 Reolink NVR 暗色主题
- 选中 Tab 高亮下划线 + 浅色背景
- 悬停效果 + 平滑过渡
- 图标 + 文字标签
"""

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLabel, QFrame,
)
from PyQt5.QtCore import pyqtSignal, Qt, QSize, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QFont, QColor


class NavigationBar(QWidget):
    """
    仿 NVR 风格的顶部导航栏。

    使用方式:
        nav = NavigationBar()
        nav.add_tab("🏠 实时预览")
        nav.add_tab("🧪 测试工具")
        nav.add_tab("📋 报警")
        nav.add_tab("📝 日志")
        nav.currentChanged.connect(stack.setCurrentIndex)
    """

    currentChanged = pyqtSignal(int)

    # 颜色常量
    BG_COLOR = "#16213e"
    BG_COLOR_ACTIVE = "#1a2744"
    BORDER_COLOR = "#0f1629"
    TEXT_COLOR = "#a0aec0"
    TEXT_COLOR_ACTIVE = "#ffffff"
    ACCENT_COLOR = "#3498db"         # 选中下划线颜色
    HOVER_COLOR = "#1e2d50"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tabs: list[str] = []
        self._buttons: list[QPushButton] = []
        self._current_index: int = 0
        self._setup_ui()

    def _setup_ui(self):
        self.setFixedHeight(48)
        self.setObjectName("navBar")
        self.setStyleSheet(f"""
            #navBar {{
                background-color: {self.BG_COLOR};
                border-bottom: 2px solid {self.BORDER_COLOR};
            }}
        """)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # 左侧 logo / 品牌标识
        self._brand_label = QLabel("  NVR AI Platform")
        self._brand_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self._brand_label.setStyleSheet(
            f"color: {self.TEXT_COLOR_ACTIVE}; padding: 0 16px;"
        )
        self._layout.addWidget(self._brand_label)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"color: {self.BORDER_COLOR};")
        sep.setMaximumWidth(1)
        self._layout.addWidget(sep)

        # Tab 按钮容器
        self._tabs_layout = QHBoxLayout()
        self._tabs_layout.setContentsMargins(4, 0, 4, 0)
        self._tabs_layout.setSpacing(2)
        self._layout.addLayout(self._tabs_layout)

        # 右侧弹簧
        self._layout.addStretch()

    def add_tab(self, label: str):
        """添加一个导航页签"""
        idx = len(self._tabs)
        self._tabs.append(label)

        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFont(QFont("Segoe UI", 9))
        btn.setMinimumHeight(44)

        # 用 QSS 实现选中/悬停/正常状态
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {self.TEXT_COLOR};
                border: none;
                border-radius: 6px;
                padding: 8px 18px;
                margin: 2px 1px;
            }}
            QPushButton:hover {{
                background-color: {self.HOVER_COLOR};
                color: {self.TEXT_COLOR_ACTIVE};
            }}
            QPushButton:checked {{
                background-color: {self.BG_COLOR_ACTIVE};
                color: {self.TEXT_COLOR_ACTIVE};
                border-bottom: 3px solid {self.ACCENT_COLOR};
                border-radius: 6px 6px 0 0;
            }}
        """)

        btn.clicked.connect(lambda checked, i=idx: self._on_tab_clicked(i))
        self._buttons.append(btn)
        self._tabs_layout.addWidget(btn)

        # 第一个 Tab 默认选中
        if idx == 0:
            btn.setChecked(True)

    def _on_tab_clicked(self, index: int):
        """点击 Tab 时切换选中状态"""
        self.set_current_index(index)

    def set_current_index(self, index: int):
        """通过程序设置当前选中的 Tab"""
        if index < 0 or index >= len(self._buttons):
            return

        if index == self._current_index:
            return

        # 取消旧选中
        if 0 <= self._current_index < len(self._buttons):
            self._buttons[self._current_index].setChecked(False)

        # 设置新选中
        self._current_index = index
        self._buttons[index].setChecked(True)

        # 发射信号
        self.currentChanged.emit(index)

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def tab_count(self) -> int:
        return len(self._tabs)
