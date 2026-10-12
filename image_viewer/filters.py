from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton,
    QGroupBox,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QPixmap, QImage

_DEBOUNCE_MS = 150


class FilterPanel(QWidget):
    filter_changed = Signal()
    filter_reset = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._gamma = 1.0
        self._sharpness = 0.0
        self._brightness = 1.0
        self._contrast = 1.0

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._emit_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._gamma_slider = self._add_slider(layout, "감마 보정", 10, 300, 100)
        self._sharp_slider = self._add_slider(layout, "선명도", 0, 200, 0)
        self._bright_slider = self._add_slider(layout, "밝기", 10, 300, 100)
        self._contrast_slider = self._add_slider(layout, "대비", 10, 300, 100)

        reset_btn = QPushButton("초기화")
        reset_btn.clicked.connect(self._reset)
        layout.addWidget(reset_btn)
        layout.addStretch()

        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ccc; }
            QGroupBox {
                border: 1px solid #444; border-radius: 4px;
                margin-top: 8px; padding-top: 16px;
                font-size: 12px; color: #aaa;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; }
            QSlider::groove:horizontal {
                height: 4px; background: #555; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px; height: 14px; margin: -5px 0;
                background: #0078d4; border-radius: 7px;
            }
            QSlider::sub-page:horizontal { background: #0078d4; border-radius: 2px; }
            QPushButton {
                background-color: #333; color: #eee;
                border: none; border-radius: 4px; padding: 6px;
            }
            QPushButton:hover { background-color: #555; }
            QLabel { font-size: 11px; }
        """)

    def _add_slider(self, parent_layout, name, min_val, max_val, default):
        group = QGroupBox(name)
        hlayout = QHBoxLayout(group)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(default)
        val_label = QLabel(f"{default / 100:.2f}")
        val_label.setFixedWidth(40)
        slider.valueChanged.connect(
            lambda v, lbl=val_label: (lbl.setText(f"{v / 100:.2f}"), self._on_change())
        )
        hlayout.addWidget(slider)
        hlayout.addWidget(val_label)
        parent_layout.addWidget(group)
        return slider

    def _on_change(self):
        self._gamma = self._gamma_slider.value() / 100.0
        self._sharpness = self._sharp_slider.value() / 100.0
        self._brightness = self._bright_slider.value() / 100.0
        self._contrast = self._contrast_slider.value() / 100.0
        self._debounce.start()

    def _emit_changed(self):
        self.filter_changed.emit()

    def _reset(self):
        self._gamma_slider.setValue(100)
        self._sharp_slider.setValue(0)
        self._bright_slider.setValue(100)
        self._contrast_slider.setValue(100)
        self.filter_reset.emit()

    def apply_filters(self, pixmap: QPixmap) -> QPixmap:
        if (self._gamma == 1.0 and self._sharpness == 0.0
                and self._brightness == 1.0 and self._contrast == 1.0):
            return pixmap
        try:
            from PIL import Image as PILImage
            from PIL import ImageEnhance, ImageFilter
            img = pixmap.toImage()
            if img.isNull():
                return pixmap
            img = img.convertToFormat(QImage.Format.Format_RGBA8888)
            w, h = img.width(), img.height()
            ptr = img.bits()
            if ptr is None:
                return pixmap
            data = bytes(ptr)
            pil = PILImage.frombytes("RGBA", (w, h), data)

            if self._gamma != 1.0:
                inv_gamma = 1.0 / max(self._gamma, 0.01)
                lut = [min(255, int((i / 255.0) ** inv_gamma * 255)) for i in range(256)]
                channels = pil.split()
                pil = PILImage.merge("RGBA", (
                    channels[0].point(lut),
                    channels[1].point(lut),
                    channels[2].point(lut),
                    channels[3],
                ))

            if self._brightness != 1.0:
                pil = ImageEnhance.Brightness(pil).enhance(self._brightness)

            if self._contrast != 1.0:
                pil = ImageEnhance.Contrast(pil).enhance(self._contrast)

            if self._sharpness > 0:
                pil = ImageEnhance.Sharpness(pil).enhance(1.0 + self._sharpness)

            out_data = pil.tobytes("raw", "RGBA")
            out_img = QImage(out_data, w, h, QImage.Format.Format_RGBA8888).copy()
            return QPixmap.fromImage(out_img)
        except Exception:
            return pixmap

    @property
    def is_default(self) -> bool:
        return (self._gamma == 1.0 and self._sharpness == 0.0
                and self._brightness == 1.0 and self._contrast == 1.0)
