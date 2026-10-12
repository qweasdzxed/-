import os
from PySide6.QtWidgets import (
    QScrollArea, QWidget, QVBoxLayout, QLabel, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QPixmap, QImage, QWheelEvent, QKeyEvent

_BUFFER = 3


class WebtoonView(QScrollArea):
    """Vertical continuous scroll view with lazy loading."""
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
        self._pixmaps: list[QPixmap | None] = []
        self._paths: list[str] = []
        self._loaded: set[int] = set()
        self._zoom = 1.0
        self._fit_width = True

        self._lazy_timer = QTimer(self)
        self._lazy_timer.setSingleShot(True)
        self._lazy_timer.setInterval(50)
        self._lazy_timer.timeout.connect(self._load_visible)

        self.setStyleSheet("QScrollArea { background-color: #111; border: none; }")
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def load_images(self, paths: list[str]):
        self._clear()
        self._paths = list(paths)
        self._pixmaps = [None] * len(paths)
        self._loaded = set()
        for i, path in enumerate(paths):
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            label.setFixedHeight(400)
            self._layout.addWidget(label)
            self._labels.append(label)
        self.verticalScrollBar().setValue(0)
        self._load_visible()

    def _load_single(self, idx: int) -> QPixmap | None:
        path = self._paths[idx]
        pixmap = QPixmap(path)
        if pixmap.isNull():
            try:
                from PIL import Image as PILImage
                pil = PILImage.open(path).convert("RGBA")
                data = pil.tobytes("raw", "RGBA")
                img = QImage(data, pil.width, pil.height, QImage.Format.Format_RGBA8888).copy()
                pixmap = QPixmap.fromImage(img)
            except Exception:
                return None
        if pixmap.isNull():
            return None
        return pixmap

    def _load_visible(self):
        if not self._labels:
            return
        vp_top = self.verticalScrollBar().value()
        vp_bottom = vp_top + self.viewport().height()
        vw = self.viewport().width() - 20

        first_vis = -1
        last_vis = -1
        for i, label in enumerate(self._labels):
            y = label.y()
            h = label.height()
            if y + h >= vp_top and y <= vp_bottom:
                if first_vis < 0:
                    first_vis = i
                last_vis = i

        if first_vis < 0:
            first_vis = 0
            last_vis = min(len(self._labels) - 1, _BUFFER)

        load_start = max(0, first_vis - _BUFFER)
        load_end = min(len(self._labels) - 1, last_vis + _BUFFER)

        for i in range(load_start, load_end + 1):
            if i in self._loaded:
                continue
            pixmap = self._load_single(i)
            if pixmap is None:
                continue
            self._pixmaps[i] = pixmap
            self._loaded.add(i)
            if self._fit_width:
                w = int(vw * self._zoom)
            else:
                w = int(pixmap.width() * self._zoom)
            scaled = pixmap.scaledToWidth(
                max(w, 10), Qt.TransformationMode.FastTransformation
            )
            self._labels[i].setPixmap(scaled)
            self._labels[i].setFixedHeight(scaled.height())

    def _clear(self):
        for label in self._labels:
            self._layout.removeWidget(label)
            label.deleteLater()
        self._labels.clear()
        self._pixmaps.clear()
        self._paths.clear()
        self._loaded.clear()

    def _apply_zoom(self):
        vw = self.viewport().width() - 20
        for i, label in enumerate(self._labels):
            pixmap = self._pixmaps[i] if i < len(self._pixmaps) else None
            if pixmap is None:
                continue
            if self._fit_width:
                w = int(vw * self._zoom)
            else:
                w = int(pixmap.width() * self._zoom)
            scaled = pixmap.scaledToWidth(
                max(w, 10), Qt.TransformationMode.FastTransformation
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
            self._load_visible()
            self.ensureWidgetVisible(self._labels[index])

    def _on_scroll(self):
        self._lazy_timer.start()
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
