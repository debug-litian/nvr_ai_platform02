"""
search_vocabulary_panel.py — 文搜图词库看板

分类展示 NVR 平台支持的文搜图语义词汇体系。
用户点击词汇 → 自动填入搜索框 → 触发 CLIP 文搜图。

词汇体系（来源：NVR 平台的文搜图词库 GUI 看板）：

👤 人
  ├─ 性别: 男, 女
  ├─ 年龄: 老人, 青年, 儿童
  ├─ 上衣: 衬衫, 长袖, 短袖, 格子衫, 条纹衫, 外套, 连衣裙
  ├─ 上衣颜色: 红, 橙, 黄, 绿, 青, 蓝, 紫, 黑, 白, 灰, 粉, 棕
  ├─ 下装: 牛仔裤, 短裤, 裙子
  └─ 下装颜色: 红, 橙, 黄, 绿, 青, 蓝, 紫, 黑, 白, 灰, 粉, 棕

🚗 机动车
  ├─ 类型: 轿车, SUV, 皮卡, 大货车, 面包车, 公交车, 摩托车
  └─ 颜色: 红, 橙, 黄, 绿, 青, 蓝, 紫, 黑, 白, 灰, 银, 棕

🚲 非机动车
  └─ 自行车, 轮椅

🐱 动物
  └─ 猫, 狗, 鸟, 松鼠, 鹿, 熊, 浣熊, 刺猬, 獾, 负鼠, 狐狸, 貂
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTreeWidget, QTreeWidgetItem, QLineEdit, QGroupBox,
    QScrollArea, QWidget, QGridLayout, QSizePolicy, QFrame,
    QSplitter, QTextEdit,
)
from PyQt5.QtCore import Qt, pyqtSignal, QSize
from PyQt5.QtGui import QFont, QColor


# ═══════════════════════════════════════════════════════════
# 词库数据
# ═══════════════════════════════════════════════════════════

VOCABULARY = {
    "👤 人": {
        "icon": "👤",
        "attributes": {
            "性别": ["男", "女"],
            "年龄": ["老人", "青年", "儿童"],
            "上衣": ["衬衫", "长袖", "短袖", "格子衫", "条纹衫", "外套", "连衣裙"],
            "上衣颜色": ["红", "橙", "黄", "绿", "青", "蓝", "紫", "黑", "白", "灰", "粉", "棕"],
            "下装": ["牛仔裤", "短裤", "裙子"],
            "下装颜色": ["红", "橙", "黄", "绿", "青", "蓝", "紫", "黑", "白", "灰", "粉", "棕"],
        },
    },
    "🚗 机动车": {
        "icon": "🚗",
        "attributes": {
            "类型": ["轿车", "SUV", "皮卡", "大货车", "面包车", "公交车", "摩托车"],
            "颜色": ["红", "橙", "黄", "绿", "青", "蓝", "紫", "黑", "白", "灰", "银", "棕"],
        },
    },
    "🚲 非机动车": {
        "icon": "🚲",
        "attributes": {
            "类型": ["自行车", "轮椅"],
        },
    },
    "🐱 动物": {
        "icon": "🐱",
        "attributes": {
            "类型": ["猫", "狗", "鸟", "松鼠", "鹿", "熊", "浣熊", "刺猬", "獾", "负鼠", "狐狸", "貂"],
        },
    },
}

# 颜色标签的色块映射（用于颜色词汇的可视化展示）
COLOR_SWATCHES = {
    "红": "#e74c3c", "橙": "#e67e22", "黄": "#f1c40f", "绿": "#2ecc71",
    "青": "#1abc9c", "蓝": "#3498db", "紫": "#9b59b6", "黑": "#2c3e50",
    "白": "#ecf0f1", "灰": "#95a5a6", "粉": "#fd79a8", "棕": "#8B4513",
    "银": "#bdc3c7",
}


class ColorChipButton(QPushButton):
    """带颜色色块的小按钮，用于上衣颜色/下装颜色/车辆颜色"""

    clicked_with_text = pyqtSignal(str)

    def __init__(self, text: str, color_hex: str, parent=None):
        super().__init__(text, parent)
        self._text = text
        self.setFixedSize(42, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f'搜索 "{text}"')

        bg = color_hex
        text_color = "#ffffff" if color_hex not in ("#ecf0f1", "#f1c40f") else "#333333"

        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                color: {text_color};
                border: 1px solid #ccc;
                border-radius: 4px;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                border: 2px solid #3498db;
            }}
        """)
        self.clicked.connect(lambda: self.clicked_with_text.emit(self._text))


class VocabularyChip(QPushButton):
    """通用词汇标签按钮"""

    clicked_with_text = pyqtSignal(str)

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self._text = text
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMinimumHeight(28)
        self.setStyleSheet("""
            QPushButton {
                background-color: #e3f2fd;
                color: #1565c0;
                border: 1px solid #bbdefb;
                border-radius: 4px;
                padding: 3px 10px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #bbdefb;
                border-color: #64b5f6;
            }
        """)
        self.clicked.connect(lambda: self.clicked_with_text.emit(self._text))


class SearchVocabularyPanel(QDialog):
    """
    文搜图词库看板对话框。

    使用方式:
        panel = SearchVocabularyPanel(self)
        panel.word_selected.connect(search_input.setText)
        panel.search_requested.connect(search_button.click)
        panel.exec_()
    """

    word_selected = pyqtSignal(str)      # 用户点击某个词
    search_requested = pyqtSignal(str)   # 用户点击"搜索"按钮

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📖 文搜图词库")
        self.resize(720, 560)
        self.setMinimumSize(600, 400)

        self._selected_words: list[str] = []
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # 标题
        title = QLabel("📖 文搜图词库看板")
        title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        main_layout.addWidget(title)

        desc = QLabel("点击词汇添加到搜索框，支持多词组合搜索。颜色类词汇支持色块可视化。")
        desc.setStyleSheet("color: #666; font-size: 11px;")
        desc.setWordWrap(True)
        main_layout.addWidget(desc)

        # 滚动区域 — 分类展示所有词汇
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(10)

        # 按大类构建分组
        for category, cat_data in VOCABULARY.items():
            group = self._build_category_group(category, cat_data)
            scroll_layout.addWidget(group)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll, stretch=1)

        # 底部：选中词 + 操作按钮
        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 8, 0, 0)

        bottom_layout.addWidget(QLabel("已选词:"))

        self.selected_display = QLineEdit()
        self.selected_display.setReadOnly(True)
        self.selected_display.setPlaceholderText("点击上方词汇添加...")
        bottom_layout.addWidget(self.selected_display, stretch=1)

        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self._on_clear)
        bottom_layout.addWidget(self.btn_clear)

        self.btn_search = QPushButton("🔍 搜索")
        self.btn_search.setDefault(True)
        self.btn_search.clicked.connect(self._on_search)
        bottom_layout.addWidget(self.btn_search)

        main_layout.addWidget(bottom)

    def _build_category_group(self, category: str, cat_data: dict) -> QGroupBox:
        """构建一个大类的分组框"""
        group = QGroupBox(category)
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 13px;
                border: 1px solid #ddd;
                border-radius: 6px;
                margin-top: 8px;
                padding: 12px 8px 8px 8px;
                background: #fafafa;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #333;
            }
        """)

        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        for attr_name, words in cat_data["attributes"].items():
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 2, 8, 2)
            row_layout.setSpacing(4)

            # 属性名标签
            attr_label = QLabel(f"{attr_name}:")
            attr_label.setFixedWidth(72)
            attr_label.setStyleSheet("color: #555; font-size: 12px; font-weight: normal;")
            attr_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row_layout.addWidget(attr_label)

            # 词汇标签流
            for word in words:
                color_hex = COLOR_SWATCHES.get(word)
                if color_hex:
                    chip = ColorChipButton(word, color_hex)
                else:
                    chip = VocabularyChip(word)
                chip.clicked_with_text.connect(self._on_word_clicked)
                row_layout.addWidget(chip)

            row_layout.addStretch()
            layout.addWidget(row)

        return group

    def _on_word_clicked(self, word: str):
        """点击某个词汇"""
        if word not in self._selected_words:
            self._selected_words.append(word)

        # 更新显示
        self.selected_display.setText(" ".join(self._selected_words))
        self.word_selected.emit(word)

    def _on_clear(self):
        """清空选中"""
        self._selected_words.clear()
        self.selected_display.clear()

    def _on_search(self):
        """触发搜索"""
        text = self.selected_display.text().strip()
        if text:
            self.search_requested.emit(text)
            self.accept()  # 关闭对话框
