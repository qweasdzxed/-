import os
from PySide6.QtWidgets import (
    QListWidget, QListWidgetItem, QAbstractItemView
)
from PySide6.QtCore import Qt, Signal, QSize, QThread, QMutex, QMutexLocker
from PySide6.QtGui import QPixmap, QIcon, QImage

THUMB_SIZE = 120
SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp",
    ".tiff", ".tif", ".ico", ".svg", ".svgz",
    ".pbm", ".pgm", ".ppm", ".xbm", ".xpm",
    ".heic", ".heif", ".avif", ".jxl",
    ".psd", ".dds", ".tga", ".pcx",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv",
    ".webm", ".m4v", ".3gp", ".mpg", ".mpeg", ".ts",
    ".vob", ".ogv", ".mts", ".m2ts", ".divx", ".xvid",
    ".f4v", ".rm", ".rmvb", ".asf", ".m2v", ".mxf",
    ".dv",
}

ALL_EXTENSIONS = SUPPORTED_EXTENSIONS | VIDEO_EXTENSIONS


def is_media_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in ALL_EXTENSIONS


def is_image_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXTENSIONS


def is_video_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def list_media_in_dir(directory: str, recursive: bool = False) -> list[str]:
    if recursive:
        result = []
        try:
            for root, _dirs, files in os.walk(directory):
                for f in sorted(files, key=str.lower):
                    if is_media_file(f):
                        result.append(os.path.join(root, f))
        except OSError:
            pass
        return result
    try:
        entries = sorted(os.listdir(directory), key=str.lower)
    except OSError:
        return []
    return [
        os.path.join(directory, e)
        for e in entries
        if is_media_file(e) and os.path.isfile(os.path.join(directory, e))
    ]


class ThumbLoader(QThread):
    thumb_ready = Signal(str, QIcon)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: list[str] = []
        self._mutex = QMutex()
        self._running = True

    def enqueue(self, paths: list[str]):
        with QMutexLocker(self._mutex):
            self._queue = list(paths)

    def stop(self):
        self._running = False
        self.wait(3000)

    def run(self):
        while self._running:
            path = None
            with QMutexLocker(self._mutex):
                if self._queue:
                    path = self._queue.pop(0)
            if path is None:
                self.msleep(50)
                continue
            pixmap = QPixmap(path)
            if pixmap.isNull():
                try:
                    from PIL import Image as PILImage
                    pil_img = PILImage.open(path)
                    pil_img = pil_img.convert("RGBA")
                    pil_img.thumbnail((THUMB_SIZE, THUMB_SIZE))
                    data = pil_img.tobytes("raw", "RGBA")
                    img = QImage(data, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888)
                    pixmap = QPixmap.fromImage(img.copy())
                except Exception:
                    continue
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    THUMB_SIZE, THUMB_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.thumb_ready.emit(path, QIcon(scaled))


class ThumbnailPanel(QListWidget):
    image_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path_to_item: dict[str, QListWidgetItem] = {}
        self._loader = ThumbLoader(self)
        self._loader.thumb_ready.connect(self._on_thumb_ready)
        self._loader.start()

        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.setSpacing(4)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMovement(QListWidget.Movement.Static)
        self.setUniformItemSizes(True)
        self.setMinimumWidth(THUMB_SIZE + 40)
        self.setMaximumWidth(THUMB_SIZE + 50)
        self.setStyleSheet("""
            QListWidget {
                background-color: #1e1e1e;
                border: none;
            }
            QListWidget::item {
                color: #ccc;
                padding: 2px;
            }
            QListWidget::item:selected {
                background-color: #0078d4;
                border-radius: 4px;
            }
        """)
        self.itemClicked.connect(self._on_item_clicked)

    def load_directory(self, image_paths: list[str]):
        self.clear()
        self._path_to_item.clear()
        placeholder = QIcon()
        for path in image_paths:
            name = os.path.basename(path)
            item = QListWidgetItem(placeholder, name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setSizeHint(QSize(THUMB_SIZE + 10, THUMB_SIZE + 24))
            self.addItem(item)
            self._path_to_item[path] = item
        self._loader.enqueue(image_paths)

    def select_path(self, path: str):
        item = self._path_to_item.get(path)
        if item:
            self.setCurrentItem(item)
            self.scrollToItem(item)

    def _on_thumb_ready(self, path: str, icon: QIcon):
        item = self._path_to_item.get(path)
        if item:
            item.setIcon(icon)

    def _on_item_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.image_selected.emit(path)

    def shutdown(self):
        self._loader.stop()
