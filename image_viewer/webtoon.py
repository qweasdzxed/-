import os
from PySide6.QtWidgets import (
    QScrollArea, QWidget, QVBoxLayout, QLabel, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPixmap, QImage, QWheelEvent, QKeyEvent


class WebtoonView(QScrollArea):
    """Vertical continuous scroll view for webtoon-style reading."""
    image_changed = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setWidget(self._container)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._labels: list[QLabel] = []
        self._pixmaps: list[QPixmap] = []
        self._paths: list[str] = []
        self._zoom = 1.0
        self._fit_width = True

        self.setStyleSheet("QScrollArea { background-color: #111; border: none; }")
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def load_images(self, paths: list[str]):
        self._clear()
        self._paths = list(paths)
        for path in paths:
            pixmap = QPixmap(path)
            if pixmap.isNull():
                try:
                    from PIL import Image as PILImage
                    pil = PILImage.open(path).convert("RGBA")
                    data = pil.tobytes("raw", "RGBA")
                    img = QImage(data, pil.width, pil.height, QImage.Format.Format_RGBA8888).copy()
                    pixmap = QPixmap.fromImage(img)
                except Exception:
                    continue
            if pixmap.isNull():
                continue
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            self._layout.addWidget(label)
            self._labels.append(label)
            self._pixmaps.append(pixmap)
        self._apply_zoom()
        self.verticalScrollBar().setValue(0)

    def _clear(self):
        for label in self._labels:
            self._layout.removeWidget(label)
            label.deleteLater()
        self._labels.clear()
        self._pixmaps.clear()
        self._paths.clear()

    def _apply_zoom(self):
        vw = self.viewport().width() - 20
        for label, pixmap in zip(self._labels, self._pixmaps):
            if self._fit_width:
                w = int(vw * self._zoom)
            else:
                w = int(pixmap.width() * self._zoom)
            scaled = pixmap.scaledToWidth(
                max(w, 10), Qt.TransformationMode.SmoothTransformation
            )
            label.setPixmap(scaled)
            label.setFixedHeight(scaled.height())

    def zoom_in(self):
        self._zoom = min(self._zoom * 1.2, 10.0)
        self._apply_zoom()

    def zoom_out(self):
        self._zoom = max(self._zoom / 1.2, 0.1)
        self._apply_zoom()

    def zoom_fit_width(self):
        self._fit_width = True
        self._zoom = 1.0
        self._apply_zoom()

    def scroll_to_image(self, index: int):
        if 0 <= index < len(self._labels):
            self.ensureWidgetVisible(self._labels[index])

    def _on_scroll(self):
        if not self._labels:
            return
        vy = self.verticalScrollBar().value() + self.viewport().height() // 3
        for i, label in enumerate(self._labels):
            y = label.y()
            h = label.height()
            if y <= vy <= y + h:
                self.image_changed.emit(i, len(self._labels))
                return

    def wheelEvent(self, event: QWheelEvent):
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_width:
            self._apply_zoom()
