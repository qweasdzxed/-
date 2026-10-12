import sys
import os
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QLabel, QToolBar,
    QDockWidget, QMessageBox, QInputDialog, QStackedWidget,
)
from PySide6.QtCore import Qt, QTimer, QSettings, QSize
from PySide6.QtGui import (
    QAction, QKeySequence, QDragEnterEvent,
    QDropEvent, QCloseEvent,
)
from canvas import ImageCanvas
from thumbnails import (
    ThumbnailPanel, list_media_in_dir, is_media_file,
    is_image_file, is_video_file, ALL_EXTENSIONS, SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS,
)
from video_player import VideoPlayer

APP_NAME = "LadyViewer"
ORG_NAME = "LadyViewer"
VERSION = "1.1.0"

MEDIA_FILTER = "미디어 파일 ({exts});;이미지 ({img});;동영상 ({vid});;모든 파일 (*)".format(
    exts=" ".join(f"*{e}" for e in sorted(ALL_EXTENSIONS)),
    img=" ".join(f"*{e}" for e in sorted(SUPPORTED_EXTENSIONS)),
    vid=" ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS)),
)


class MainWindow(QMainWindow):
    def __init__(self, initial_path: str | None = None):
        super().__init__()
        self._settings = QSettings(ORG_NAME, APP_NAME)
        self._current_path: str | None = None
        self._image_list: list[str] = []
        self._current_index: int = -1
        self._slideshow_active = False
        self._slideshow_interval = 3000
        self._showing_video = False

        self._setup_ui()
        self._setup_menu()
        self._setup_toolbar()
        self._restore_state()
        self.setAcceptDrops(True)

        if initial_path and os.path.isfile(initial_path):
            self._open_file(initial_path)
        elif initial_path and os.path.isdir(initial_path):
            media = list_media_in_dir(initial_path)
            if media:
                self._open_file(media[0])

    def _setup_ui(self):
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 800)
        self.setMinimumSize(400, 300)

        self._canvas = ImageCanvas(self)
        self._canvas.zoom_changed.connect(self._on_zoom_changed)

        self._video = VideoPlayer(self)
        self._video.playback_finished.connect(self._on_video_finished)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._canvas)
        self._stack.addWidget(self._video)

        self._thumb_panel = ThumbnailPanel(self)
        self._thumb_panel.image_selected.connect(self._open_file)

        self._thumb_dock = QDockWidget("썸네일", self)
        self._thumb_dock.setWidget(self._thumb_panel)
        self._thumb_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._thumb_dock)

        self.setCentralWidget(self._stack)

        self._status_label = QLabel()
        self._zoom_label = QLabel()
        self._index_label = QLabel()
        self.statusBar().addWidget(self._status_label, 1)
        self.statusBar().addPermanentWidget(self._index_label)
        self.statusBar().addPermanentWidget(self._zoom_label)

        self._slideshow_timer = QTimer(self)
        self._slideshow_timer.timeout.connect(self._slideshow_next)

        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QMenuBar { background-color: #2d2d2d; color: #eee; }
            QMenuBar::item:selected { background-color: #0078d4; }
            QMenu { background-color: #2d2d2d; color: #eee; border: 1px solid #555; }
            QMenu::item:selected { background-color: #0078d4; }
            QToolBar { background-color: #2d2d2d; border: none; spacing: 2px; }
            QStatusBar { background-color: #2d2d2d; color: #aaa; }
            QDockWidget { color: #eee; }
            QDockWidget::title {
                background-color: #2d2d2d;
                padding: 4px;
                text-align: center;
            }
        """)

    def _make_action(self, text, shortcut=None, callback=None, checkable=False):
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if callback:
            action.triggered.connect(callback)
        if checkable:
            action.setCheckable(True)
        return action

    def _setup_menu(self):
        file_menu = self.menuBar().addMenu("파일(&F)")
        file_menu.addAction(self._make_action("열기(&O)", "Ctrl+O", self._on_open))
        file_menu.addAction(self._make_action("폴더 열기(&D)", "Ctrl+Shift+O", self._on_open_folder))
        file_menu.addSeparator()
        file_menu.addAction(self._make_action("다른 이름으로 저장(&S)", "Ctrl+S", self._on_save_as))
        file_menu.addAction(self._make_action("클립보드로 복사", "Ctrl+C", self._on_copy))
        file_menu.addSeparator()
        file_menu.addAction(self._make_action("파일 삭제", "Delete", self._on_delete_file))
        file_menu.addSeparator()
        file_menu.addAction(self._make_action("종료(&X)", "Ctrl+Q", self.close))

        view_menu = self.menuBar().addMenu("보기(&V)")
        view_menu.addAction(self._make_action("확대", "Ctrl+=", self._canvas.zoom_in))
        view_menu.addAction(self._make_action("축소", "Ctrl+-", self._canvas.zoom_out))
        view_menu.addAction(self._make_action("원본 크기", "Ctrl+1", self._canvas.zoom_original))
        view_menu.addAction(self._make_action("창에 맞추기", "Ctrl+0", self._canvas.zoom_fit))
        view_menu.addSeparator()
        view_menu.addAction(self._make_action("전체 화면", "F11", self._toggle_fullscreen))
        view_menu.addSeparator()
        self._thumb_toggle = self._make_action("썸네일 패널", "Ctrl+T", checkable=True)
        self._thumb_toggle.setChecked(True)
        self._thumb_toggle.toggled.connect(self._thumb_dock.setVisible)
        self._thumb_dock.visibilityChanged.connect(self._thumb_toggle.setChecked)
        view_menu.addAction(self._thumb_toggle)

        image_menu = self.menuBar().addMenu("이미지(&I)")
        image_menu.addAction(self._make_action("시계 방향 회전", "Ctrl+R", self._canvas.rotate_cw))
        image_menu.addAction(self._make_action("반시계 방향 회전", "Ctrl+Shift+R", self._canvas.rotate_ccw))

        nav_menu = self.menuBar().addMenu("탐색(&N)")
        nav_menu.addAction(self._make_action("이전 파일", "Left", self._prev_image))
        nav_menu.addAction(self._make_action("다음 파일", "Right", self._next_image))
        nav_menu.addAction(self._make_action("첫 번째 파일", "Home", self._first_image))
        nav_menu.addAction(self._make_action("마지막 파일", "End", self._last_image))
        nav_menu.addSeparator()
        self._slideshow_action = self._make_action(
            "슬라이드쇼 시작", "F5", self._toggle_slideshow
        )
        nav_menu.addAction(self._slideshow_action)
        nav_menu.addAction(
            self._make_action("슬라이드쇼 간격 설정", None, self._set_slideshow_interval)
        )

        help_menu = self.menuBar().addMenu("도움말(&H)")
        help_menu.addAction(self._make_action("정보", None, self._show_about))

    def _setup_toolbar(self):
        tb = QToolBar("도구", self)
        tb.setIconSize(QSize(20, 20))
        tb.setMovable(False)
        tb.addAction(self._make_action("  <<  ", None, self._prev_image))
        tb.addAction(self._make_action("  >>  ", None, self._next_image))
        tb.addSeparator()
        tb.addAction(self._make_action("  +  ", None, self._canvas.zoom_in))
        tb.addAction(self._make_action("  -  ", None, self._canvas.zoom_out))
        tb.addAction(self._make_action(" 1:1 ", None, self._canvas.zoom_original))
        tb.addAction(self._make_action(" Fit ", None, self._canvas.zoom_fit))
        tb.addSeparator()
        tb.addAction(self._make_action(" ↻ ", None, self._canvas.rotate_cw))
        tb.addAction(self._make_action(" ↺ ", None, self._canvas.rotate_ccw))
        tb.addSeparator()
        tb.addAction(self._make_action(" ▶ ", None, self._toggle_slideshow))
        self.addToolBar(tb)

    def _on_open(self):
        start_dir = os.path.dirname(self._current_path) if self._current_path else ""
        path, _ = QFileDialog.getOpenFileName(self, "파일 열기", start_dir, MEDIA_FILTER)
        if path:
            self._open_file(path)

    def _on_open_folder(self):
        start_dir = os.path.dirname(self._current_path) if self._current_path else ""
        folder = QFileDialog.getExistingDirectory(self, "폴더 열기", start_dir)
        if folder:
            media = list_media_in_dir(folder)
            if media:
                self._open_file(media[0])

    def _open_file(self, path: str):
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            return

        if is_video_file(path):
            self._video.stop()
            self._video.load(path)
            self._stack.setCurrentWidget(self._video)
            self._showing_video = True
            self._zoom_label.setText(" VIDEO ")
        elif is_image_file(path) or is_media_file(path):
            if self._showing_video:
                self._video.stop()
            if not self._canvas.load_image(path):
                self.statusBar().showMessage(
                    f"파일을 열 수 없습니다: {os.path.basename(path)}", 3000
                )
                return
            self._stack.setCurrentWidget(self._canvas)
            self._showing_video = False
        else:
            return

        self._current_path = path
        directory = os.path.dirname(path)
        new_list = list_media_in_dir(directory)
        if new_list != self._image_list:
            self._image_list = new_list
            self._thumb_panel.load_directory(self._image_list)

        try:
            self._current_index = self._image_list.index(path)
        except ValueError:
            self._current_index = -1

        self._thumb_panel.select_path(path)
        self._update_title()
        self._update_status()

    def _update_title(self):
        if self._current_path:
            name = os.path.basename(self._current_path)
            self.setWindowTitle(f"{name} - {APP_NAME}")
        else:
            self.setWindowTitle(APP_NAME)

    def _update_status(self):
        if not self._current_path:
            return
        if not self._showing_video:
            pixmap = self._canvas.current_pixmap()
            size_str = ""
            if not pixmap.isNull():
                size_str = f"{pixmap.width()} x {pixmap.height()}"
        else:
            size_str = "동영상"
        try:
            file_size = os.path.getsize(self._current_path)
            if file_size < 1024:
                fs = f"{file_size} B"
            elif file_size < 1024 * 1024:
                fs = f"{file_size / 1024:.1f} KB"
            else:
                fs = f"{file_size / (1024 * 1024):.1f} MB"
        except OSError:
            fs = ""
        self._status_label.setText(
            f"  {os.path.basename(self._current_path)}    {size_str}    {fs}"
        )
        if self._image_list:
            self._index_label.setText(
                f" {self._current_index + 1} / {len(self._image_list)} "
            )

    def _on_zoom_changed(self, zoom: float):
        if not self._showing_video:
            self._zoom_label.setText(f" {zoom * 100:.0f}% ")

    def _on_video_finished(self):
        if self._slideshow_active:
            self._next_image()

    def _navigate(self, index: int):
        if not self._image_list or index < 0 or index >= len(self._image_list):
            return
        self._open_file(self._image_list[index])

    def _prev_image(self):
        if self._current_index > 0:
            self._navigate(self._current_index - 1)
        elif self._image_list:
            self._navigate(len(self._image_list) - 1)

    def _next_image(self):
        if self._current_index < len(self._image_list) - 1:
            self._navigate(self._current_index + 1)
        elif self._image_list:
            self._navigate(0)

    def _first_image(self):
        self._navigate(0)

    def _last_image(self):
        if self._image_list:
            self._navigate(len(self._image_list) - 1)

    def _toggle_slideshow(self):
        if self._slideshow_active:
            self._slideshow_timer.stop()
            self._slideshow_active = False
            self._slideshow_action.setText("슬라이드쇼 시작")
        else:
            self._slideshow_timer.start(self._slideshow_interval)
            self._slideshow_active = True
            self._slideshow_action.setText("슬라이드쇼 중지")

    def _slideshow_next(self):
        if not self._showing_video:
            self._next_image()

    def _set_slideshow_interval(self):
        val, ok = QInputDialog.getInt(
            self, "슬라이드쇼 간격",
            "간격 (초):", self._slideshow_interval // 1000, 1, 60
        )
        if ok:
            self._slideshow_interval = val * 1000
            if self._slideshow_active:
                self._slideshow_timer.setInterval(self._slideshow_interval)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.menuBar().show()
            self.statusBar().show()
        else:
            self.menuBar().hide()
            self.statusBar().hide()
            self.showFullScreen()

    def _on_save_as(self):
        if not self._current_path or self._showing_video:
            return
        pixmap = self._canvas.current_pixmap()
        if pixmap.isNull():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "다른 이름으로 저장", self._current_path,
            "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp);;WebP (*.webp)"
        )
        if path:
            pixmap.save(path)

    def _on_copy(self):
        if self._showing_video:
            return
        pixmap = self._canvas.current_pixmap()
        if not pixmap.isNull():
            QApplication.clipboard().setPixmap(pixmap)
            self.statusBar().showMessage("클립보드에 복사됨", 2000)

    def _on_delete_file(self):
        if not self._current_path:
            return
        name = os.path.basename(self._current_path)
        reply = QMessageBox.question(
            self, "파일 삭제",
            f"정말로 '{name}'을(를) 삭제하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                path_to_delete = self._current_path
                if self._showing_video:
                    self._video.stop()
                if self._current_index < len(self._image_list) - 1:
                    self._next_image()
                elif self._current_index > 0:
                    self._prev_image()
                else:
                    self._current_path = None
                    self._current_index = -1
                os.remove(path_to_delete)
                if path_to_delete in self._image_list:
                    self._image_list.remove(path_to_delete)
                self._thumb_panel.load_directory(self._image_list)
                if self._current_path:
                    self._thumb_panel.select_path(self._current_path)
                    self._current_index = self._image_list.index(self._current_path)
                self._update_status()
            except OSError as e:
                QMessageBox.warning(self, "오류", f"파일 삭제 실패: {e}")

    def _show_about(self):
        QMessageBox.about(
            self, f"{APP_NAME} 정보",
            f"<h2>{APP_NAME}</h2>"
            f"<p>버전 {VERSION}</p>"
            f"<p>가볍고 빠른 이미지/동영상 뷰어</p>"
            f"<p>Python + PySide6 (Qt)</p>"
        )

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isfile(path) and is_media_file(path):
                self._open_file(path)
            elif os.path.isdir(path):
                media = list_media_in_dir(path)
                if media:
                    self._open_file(media[0])

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self._toggle_fullscreen()
                return
            if self._slideshow_active:
                self._toggle_slideshow()
                return
        if event.key() == Qt.Key.Key_Space and self._showing_video:
            self._video._toggle_play()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent):
        self._save_state()
        self._thumb_panel.shutdown()
        self._video.stop()
        if self._slideshow_active:
            self._slideshow_timer.stop()
        super().closeEvent(event)

    def _save_state(self):
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("windowState", self.saveState())
        self._settings.setValue("thumb_visible", self._thumb_dock.isVisible())

    def _restore_state(self):
        geom = self._settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)
        state = self._settings.value("windowState")
        if state:
            self.restoreState(state)
        thumb_vis = self._settings.value("thumb_visible", True, type=bool)
        self._thumb_dock.setVisible(thumb_vis)
        self._thumb_toggle.setChecked(thumb_vis)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setStyle("Fusion")

    initial_path = None
    if len(sys.argv) > 1:
        candidate = sys.argv[1]
        if os.path.exists(candidate):
            initial_path = os.path.abspath(candidate)

    window = MainWindow(initial_path)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
