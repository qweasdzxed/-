from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPixmapItem
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QTimer
from PySide6.QtGui import QPixmap, QImage, QWheelEvent, QMouseEvent, QPainter, QMovie


class ImageCanvas(QGraphicsView):
    zoom_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item = QGraphicsPixmapItem()
        self._scene.addItem(self._pixmap_item)
        self._zoom = 1.0
        self._min_zoom = 0.01
        self._max_zoom = 50.0
        self._fit_mode = True
        self._movie = None
        self._dragging = False
        self._drag_start = QPointF()

        self._anim_frames: list[QPixmap] = []
        self._anim_durations: list[int] = []
        self._anim_index = 0
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._on_anim_tick)

        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setBackgroundBrush(Qt.GlobalColor.black)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)

    def load_image(self, path: str) -> bool:
        self._stop_animation()
        lower = path.lower()
        if lower.endswith((".gif", ".webp", ".png", ".apng")):
            if self._try_load_pillow_anim(path):
                return True
            if lower.endswith(".gif"):
                if self._load_qmovie(path):
                    return True
        img = QImage(path)
        if img.isNull():
            try:
                from PIL import Image as PILImage
                pil_img = PILImage.open(path)
                pil_img = pil_img.convert("RGBA")
                data = pil_img.tobytes("raw", "RGBA")
                img = QImage(data, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888)
                if img.isNull():
                    return False
                img = img.copy()
            except Exception:
                return False
        pixmap = QPixmap.fromImage(img)
        self._pixmap_item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        if self._fit_mode:
            self.fit_in_view()
        return True

    def _try_load_pillow_anim(self, path: str) -> bool:
        try:
            from PIL import Image as PILImage
            pil_img = PILImage.open(path)
            n_frames = getattr(pil_img, "n_frames", 1)
            if n_frames <= 1:
                return False
            frames = []
            durations = []
            for i in range(n_frames):
                pil_img.seek(i)
                frame = pil_img.convert("RGBA")
                data = frame.tobytes("raw", "RGBA")
                qimg = QImage(data, frame.width, frame.height, QImage.Format.Format_RGBA8888).copy()
                frames.append(QPixmap.fromImage(qimg))
                dur = pil_img.info.get("duration", 100)
                durations.append(max(dur, 10))
            self._anim_frames = frames
            self._anim_durations = durations
            self._anim_index = 0
            first = self._anim_frames[0]
            self._pixmap_item.setPixmap(first)
            self._scene.setSceneRect(QRectF(first.rect()))
            if self._fit_mode:
                self.fit_in_view()
            self._anim_timer.start(self._anim_durations[0])
            return True
        except Exception:
            return False

    def _on_anim_tick(self):
        if not self._anim_frames:
            return
        self._anim_index = (self._anim_index + 1) % len(self._anim_frames)
        self._pixmap_item.setPixmap(self._anim_frames[self._anim_index])
        self._anim_timer.setInterval(self._anim_durations[self._anim_index])

    def _load_qmovie(self, path: str) -> bool:
        self._movie = QMovie(path)
        if not self._movie.isValid():
            self._movie = None
            return False
        self._movie.setCacheMode(QMovie.CacheMode.CacheAll)
        self._movie.frameChanged.connect(self._on_frame_changed)
        self._movie.start()
        first_frame = self._movie.currentPixmap()
        self._pixmap_item.setPixmap(first_frame)
        self._scene.setSceneRect(QRectF(first_frame.rect()))
        if self._fit_mode:
            self.fit_in_view()
        return True

    def _on_frame_changed(self):
        if self._movie:
            self._pixmap_item.setPixmap(self._movie.currentPixmap())

    def _stop_animation(self):
        self._anim_timer.stop()
        self._anim_frames.clear()
        self._anim_durations.clear()
        self._anim_index = 0
        if self._movie:
            self._movie.stop()
            self._movie.frameChanged.disconnect(self._on_frame_changed)
            self._movie = None

    def current_pixmap(self) -> QPixmap:
        return self._pixmap_item.pixmap()

    def fit_in_view(self):
        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull():
            return
        self.resetTransform()
        vr = self.viewport().rect()
        sr = self._scene.sceneRect()
        if sr.width() == 0 or sr.height() == 0:
            return
        x_ratio = vr.width() / sr.width()
        y_ratio = vr.height() / sr.height()
        ratio = min(x_ratio, y_ratio)
        if ratio > 1.0:
            ratio = 1.0
        self._zoom = ratio
        self.scale(ratio, ratio)
        self.centerOn(self._pixmap_item)
        self.zoom_changed.emit(self._zoom)

    def set_zoom(self, factor: float):
        factor = max(self._min_zoom, min(self._max_zoom, factor))
        self.resetTransform()
        self._zoom = factor
        self._fit_mode = False
        self.scale(factor, factor)
        self.zoom_changed.emit(self._zoom)

    def zoom_in(self):
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self):
        self.set_zoom(self._zoom / 1.25)

    def zoom_original(self):
        self._fit_mode = False
        self.set_zoom(1.0)

    def zoom_fit(self):
        self._fit_mode = True
        self.fit_in_view()

    def rotate_cw(self):
        self.rotate(90)

    def rotate_ccw(self):
        self.rotate(-90)

    def wheelEvent(self, event: QWheelEvent):
        if event.angleDelta().y() > 0:
            self.set_zoom(self._zoom * 1.15)
        else:
            self.set_zoom(self._zoom / 1.15)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            delta = event.position() - self._drag_start
            self._drag_start = event.position()
            self._fit_mode = False
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x())
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y())
            )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._fit_mode:
                self.zoom_original()
            else:
                self.zoom_fit()
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event):
        if self._fit_mode:
            self.fit_in_view()
        super().resizeEvent(event)
