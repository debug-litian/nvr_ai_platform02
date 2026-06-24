from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QListWidget, QPushButton
from PyQt5.QtCore import pyqtSignal


class SearchPanel(QWidget):
    searchRequested = pyqtSignal(str)
    resultActivated = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.input = QLineEdit(self)
        self.input.setPlaceholderText("Enter search text...")
        self.btn = QPushButton("Search", self)
        self.list = QListWidget(self)
        self.layout.addWidget(self.input)
        self.layout.addWidget(self.btn)
        self.layout.addWidget(self.list)
        self.btn.clicked.connect(self._on_search)
        self.list.itemActivated.connect(self._on_activate)

    def _on_search(self):
        t = self.input.text().strip()
        if t:
            self.searchRequested.emit(t)

    def show_results(self, results):
        self.list.clear()
        for r in results:
            meta = r.get("meta", {})
            score = r.get("score", 0)
            item_text = f"{meta.get('video','')}: {meta.get('ts','')} ({score:.3f})"
            self.list.addItem(item_text)
            self.list.item(self.list.count()-1).setData(1000, r)

    def _on_activate(self, item):
        r = item.data(1000)
        if r:
            self.resultActivated.emit(r)
