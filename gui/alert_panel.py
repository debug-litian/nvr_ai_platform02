from PyQt5.QtWidgets import QWidget, QVBoxLayout, QListWidget


class AlertPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.list = QListWidget(self)
        self.layout.addWidget(self.list)

    def add_alert(self, text: str):
        self.list.addItem(text)
