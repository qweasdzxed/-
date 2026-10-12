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
from webtoon import WebtoonView
from metadata import MetadataPanel
from filters import FilterPanel

APP_NAME = "LadyViewer"
ORG_NAME = "LadyViewer"
VERSION = "2.0.0"

MEDIA_FILTER = "미디어 파일 ({exts});;이미지 ({img});;동영상 ({vid});;모든 파일 (*)".format(
    exts=" ".join(f"*{e}" for e in sorted(ALL_EXTENSIONS)),
    img=" ".join(f"*{e}" for e in sorted(SUPPORTED_EXTENSIONS)),
    vid=" ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS)),
)

MODE_IMAGE = 0
MODE_VIDEO = 1
MODE_WEBTOON = 2


class MainWindow(QMainWindow):
    def __init__(self, initial_path: str | None = None):
        super().__init__()
        self._settings = QSettings(ORG_NAME, APP_NAME)
        self._current_path: str | None = None
        self._current_dir: str | None = None
        self._image_list: list[str] = []
        self._current_index: int = -1
        self._slideshow_active = False
        self._slideshow_interval = 3000
        self._view_mode = MODE_IMAGE
        self._recursive = False

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

        self._webtoon = WebtoonView(self)
        self._webtoon.image_changed.connect(self._on_webtoon_scroll)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._canvas)
        self._stack.addWidget(self._video)
        self._stack.addWidget(self._webtoon)

        self._thumb_panel = ThumbnailPanel(self)
        self._thumb_panel.image_selected.connect(self._on_thumb_clicked)

        self._thumb_dock = QDockWidget("썸네일", self)
        self._thumb_dock.setWidget(self._thumb_panel)
        self._thumb_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._thumb_dock)

        self._meta_panel = MetadataPanel(self)
        self._meta_dock = QDockWidget("메타데이터", self)
        self._meta_dock.setWidget(self._meta_panel)
        self._meta_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._meta_dock)
        self._meta_dock.hide()

        self._filter_panel = FilterPanel(self)
        self._filter_panel.filter_changed.connect(self._apply_filter)
        self._filter_panel.filter_reset.connect(self._reload_current)
        self._filter_dock = QDockWidget("이미지 필터", self)
        self._filter_dock.setWidget(self._filter_panel)
        self._filter_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._filter_dock)
        self._filter_dock.hide()

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
        file_menu.addAction(self._make_action("폴더 열기 (하위 포함)(&R)", None, self._on_open_folder_recursive))
        file_menu.addSeparator()
        file_menu.addAction(self._make_action("다른 이름으로 저장(&S)", "Ctrl+S", self._on_save_as))
        file_menu.addAction(self._make_action("클립보드로 복사", "Ctrl+C", self._on_copy))
        file_menu.addSeparator()
        file_menu.addAction(self._make_action("파일 삭제", "Delete", self._on_delete_file))
        file_menu.addSeparator()
        file_menu.addAction(self._make_action("종료(&X)", "Ctrl+Q", self.close))

        view_menu = self.menuBar().addMenu("보기(&V)")
        view_menu.addAction(self._make_action("확대", "Ctrl+=", self._do_zoom_in))
        view_menu.addAction(self._make_action("축소", "Ctrl+-", self._do_zoom_out))
        view_menu.addAction(self._make_action("원본 크기", "Ctrl+1", self._canvas.zoom_original))
        view_menu.addAction(self._make_action("창에 맞추기", "Ctrl+0", self._do_zoom_fit))
        view_menu.addSeparator()
        view_menu.addAction(self._make_action("전체 화면", "F11", self._toggle_fullscreen))
        view_menu.addSeparator()

        self._webtoon_action = self._make_action(
            "웹툰 보기 모드", "Ctrl+W", self._toggle_webtoon, checkable=True
        )
        view_menu.addAction(self._webtoon_action)
        view_menu.addSeparator()

        self._thumb_toggle = self._make_action("썸네일 패널", "Ctrl+T", checkable=True)
        self._thumb_toggle.setChecked(True)
        self._thumb_toggle.toggled.connect(self._thumb_dock.setVisible)
        self._thumb_dock.visibilityChanged.connect(self._thumb_toggle.setChecked)
        view_menu.addAction(self._thumb_toggle)

        self._meta_toggle = self._make_action("메타데이터 패널", "Ctrl+M", checkable=True)
        self._meta_toggle.setChecked(False)
        self._meta_toggle.toggled.connect(self._meta_dock.setVisible)
        self._meta_dock.visibilityChanged.connect(self._meta_toggle.setChecked)
        view_menu.addAction(self._meta_toggle)

        self._filter_toggle = self._make_action("이미지 필터 패널", "Ctrl+F", checkable=True)
        self._filter_toggle.setChecked(False)
        self._filter_toggle.toggled.connect(self._filter_dock.setVisible)
        self._filter_dock.visibilityChanged.connect(self._filter_toggle.setChecked)
        view_menu.addAction(self._filter_toggle)

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
        tb.addAction(self._make_action("  +  ", None, self._do_zoom_in))
        tb.addAction(self._make_action("  -  ", None, self._do_zoom_out))
        tb.addAction(self._make_action(" 1:1 ", None, self._canvas.zoom_original))
        tb.addAction(self._make_action(" Fit ", None, self._do_zoom_fit))
        tb.addSeparator()
        tb.addAction(self._make_action(" ↻ ", None, self._canvas.rotate_cw))
        tb.addAction(self._make_action(" ↺ ", None, self._canvas.rotate_ccw))
        tb.addSeparator()
        tb.addAction(self._make_action(" ▶ ", None, self._toggle_slideshow))
        tb.addSeparator()
        self._webtoon_tb = self._make_action(" 웹툰 ", None, self._toggle_webtoon, checkable=True)
        tb.addAction(self._webtoon_tb)
        self.addToolBar(tb)

    # -- Zoom helpers that route to active view --

    def _do_zoom_in(self):
        if self._view_mode == MODE_WEBTOON:
            self._webtoon.zoom_in()
        else:
            self._canvas.zoom_in()

    def _do_zoom_out(self):
        if self._view_mode == MODE_WEBTOON:
            self._webtoon.zoom_out()
        else:
            self._canvas.zoom_out()

    def _do_zoom_fit(self):
        if self._view_mode == MODE_WEBTOON:
            self._webtoon.zoom_fit_width()
        else:
            self._canvas.zoom_fit()

    # -- File open --

    def _on_open(self):
        start_dir = os.path.dirname(self._current_path) if self._current_path else ""
        path, _ = QFileDialog.getOpenFileName(self, "파일 열기", start_dir, MEDIA_FILTER)
        if path:
            self._open_file(path)

    def _on_open_folder(self):
        start_dir = os.path.dirname(self._current_path) if self._current_path else ""
        folder = QFileDialog.getExistingDirectory(self, "폴더 열기", start_dir)
        if folder:
            self._recursive = False
            media = list_media_in_dir(folder, recursive=False)
            if media:
                self._open_file(media[0])

    def _on_open_folder_recursive(self):
        start_dir = os.path.dirname(self._current_path) if self._current_path else ""
        folder = QFileDialog.getExistingDirectory(self, "폴더 열기 (하위 포함)", start_dir)
        if folder:
            self._recursive = True
            media = list_media_in_dir(folder, recursive=True)
            if media:
                self._current_dir = folder
                self._image_list = media
                self._thumb_panel.load_directory(self._image_list)
                self._open_file(media[0])

    def _on_thumb_clicked(self, path: str):
        if self._view_mode == MODE_WEBTOON:
            try:
                idx = self._image_list.index(path)
                self._webtoon.scroll_to_image(idx)
                self._current_index = idx
                self._current_path = path
                self._update_title()
                self._update_status()
                if self._meta_dock.isVisible():
                    self._meta_panel.load_metadata(path)
            except ValueError:
                pass
        else:
            self._open_file(path)

    def _open_file(self, path: str):
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            return

        directory = os.path.dirname(path)
        if not self._recursive or self._current_dir is None:
            new_list = list_media_in_dir(directory, recursive=self._recursive)
            if new_list != self._image_list:
                self._image_list = new_list
                self._current_dir = directory
                self._thumb_panel.load_directory(self._image_list)

        try:
            self._current_index = self._image_list.index(path)
        except ValueError:
            self._current_index = -1

        if self._view_mode == MODE_WEBTOON:
            self._current_path = path
            self._thumb_panel.select_path(path)
            self._update_title()
            self._update_status()
            if self._meta_dock.isVisible():
                self._meta_panel.load_metadata(path)
            return

        if is_video_file(path):
            self._video.stop()
            self._video.load(path)
            self._stack.setCurrentIndex(MODE_VIDEO)
            self._view_mode = MODE_VIDEO
            self._zoom_label.setText(" VIDEO ")
        elif is_image_file(path) or is_media_file(path):
            if self._view_mode == MODE_VIDEO:
                self._video.stop()
            if not self._canvas.load_image(path):
                self.statusBar().showMessage(
                    f"파일을 열 수 없습니다: {os.path.basename(path)}", 3000
                )
                return
            self._stack.setCurrentIndex(MODE_IMAGE)
            self._view_mode = MODE_IMAGE
            if not self._filter_panel.is_default:
                self._apply_filter()
        else:
            return

        self._current_path = path
        self._thumb_panel.select_path(path)
        self._update_title()
        self._update_status()
        if self._meta_dock.isVisible():
            self._meta_panel.load_metadata(path)
        self._preload_adjacent()

    def _preload_adjacent(self):
        if not self._image_list or self._current_index < 0:
            return
        to_preload = []
        for offset in (-1, 1, -2, 2):
            idx = self._current_index + offset
            if 0 <= idx < len(self._image_list):
                p = self._image_list[idx]
                if is_image_file(p):
                    to_preload.append(p)
        if to_preload:
            self._canvas.preload(to_preload)

    # -- Webtoon mode --

    def _toggle_webtoon(self):
        if self._view_mode == MODE_WEBTOON:
            self._view_mode = MODE_IMAGE
            self._webtoon_action.setChecked(False)
            self._webtoon_tb.setChecked(False)
            if self._current_path:
                self._open_file(self._current_path)
        else:
            if self._view_mode == MODE_VIDEO:
                self._video.stop()
            self._view_mode = MODE_WEBTOON
            self._webtoon_action.setChecked(True)
            self._webtoon_tb.setChecked(True)
            image_paths = [p for p in self._image_list if is_image_file(p)]
            self._webtoon.load_images(image_paths)
            self._stack.setCurrentIndex(MODE_WEBTOON)
            if self._current_index >= 0:
                self._webtoon.scroll_to_image(self._current_index)
            self._zoom_label.setText(" 웹툰 ")

    def _on_webtoon_scroll(self, index: int, total: int):
        image_paths = [p for p in self._image_list if is_image_file(p)]
        if 0 <= index < len(image_paths):
            self._current_path = image_paths[index]
            try:
                self._current_index = self._image_list.index(self._current_path)
            except ValueError:
                self._current_index = index
            self._thumb_panel.select_path(self._current_path)
            self._update_title()
            self._index_label.setText(f" {index + 1} / {total} ")

    # -- Filters --

    def _apply_filter(self):
        if self._view_mode != MODE_IMAGE or not self._current_path:
            return
        self._canvas.load_image(self._current_path)
        original = self._canvas.current_pixmap()
        filtered = self._filter_panel.apply_filters(original)
        from PySide6.QtCore import QRectF
        self._canvas._pixmap_item.setPixmap(filtered)

    def _reload_current(self):
        if self._current_path and self._view_mode == MODE_IMAGE:
            self._canvas.load_image(self._current_path)

    # -- Status --

    def _update_title(self):
        if self._current_path:
            name = os.path.basename(self._current_path)
            mode = ""
            if self._view_mode == MODE_WEBTOON:
                mode = " [웹툰]"
            self.setWindowTitle(f"{name} - {APP_NAME}{mode}")
        else:
            self.setWindowTitle(APP_NAME)

    def _update_status(self):
        if not self._current_path:
            return
        if self._view_mode == MODE_VIDEO:
            size_str = "동영상"
        elif self._view_mode != MODE_WEBTOON:
            pixmap = self._canvas.current_pixmap()
            size_str = ""
            if not pixmap.isNull():
                size_str = f"{pixmap.width()} x {pixmap.height()}"
        else:
            size_str = "웹툰 보기"
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
        if self._view_mode == MODE_IMAGE:
            self._zoom_label.setText(f" {zoom * 100:.0f}% ")

    def _on_video_finished(self):
        if self._slideshow_active:
            self._next_image()

    # -- Navigation --

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

    # -- Slideshow --

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
        if self._view_mode != MODE_VIDEO:
            self._next_image()

    def _set_slideshow_interval(self):
        val, ok = QInputDialog.getDouble(
            self, "슬라이드쇼 간격",
            "간격 (초):", self._slideshow_interval / 1000.0, 0.1, 60.0, 1
        )
        if ok:
            self._slideshow_interval = int(val * 1000)
            if self._slideshow_active:
                self._slideshow_timer.setInterval(self._slideshow_interval)

    # -- Fullscreen --

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.menuBar().show()
            self.statusBar().show()
        else:
            self.menuBar().hide()
            self.statusBar().hide()
            self.showFullScreen()

    # -- File operations --

    def _on_save_as(self):
        if not self._current_path or self._view_mode == MODE_VIDEO:
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
        if self._view_mode == MODE_VIDEO:
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
                if self._view_mode == MODE_VIDEO:
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
            f"<h2>{APP_NAME} v{VERSION}</h2>"
            f"<p>가볍고 빠른 이미지/동영상 뷰어</p>"
            f"<p><b>주요 기능:</b></p>"
            f"<ul>"
            f"<li>웹툰 보기 모드 (세로 연속 스크롤)</li>"
            f"<li>SD/ComfyUI 프롬프트 메타데이터 뷰어</li>"
            f"<li>EXIF/XMP 전체 정보 보기</li>"
            f"<li>이미지 필터 (감마, 선명도, 밝기, 대비)</li>"
            f"<li>동영상 재생 (MP4, MKV, WebM 등)</li>"
            f"<li>애니메이션 GIF/WebP/APNG</li>"
            f"<li>하위 폴더 포함 열기</li>"
            f"<li>소수점 슬라이드쇼 간격</li>"
            f"</ul>"
            f"<p>Python + PySide6 (Qt)</p>"
        )

    # -- Events --

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
        if event.key() == Qt.Key.Key_Space and self._view_mode == MODE_VIDEO:
            self._video._toggle_play()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent):
        self._save_state()
        self._thumb_panel.shutdown()
        self._canvas.shutdown()
        self._video.stop()
        if self._slideshow_active:
            self._slideshow_timer.stop()
        super().closeEvent(event)

    def _save_state(self):
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("windowState", self.saveState())
        self._settings.setValue("thumb_visible", self._thumb_dock.isVisible())
        self._settings.setValue("meta_visible", self._meta_dock.isVisible())
        self._settings.setValue("filter_visible", self._filter_dock.isVisible())

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
        meta_vis = self._settings.value("meta_visible", False, type=bool)
        self._meta_dock.setVisible(meta_vis)
        self._meta_toggle.setChecked(meta_vis)
        filter_vis = self._settings.value("filter_visible", False, type=bool)
        self._filter_dock.setVisible(filter_vis)
        self._filter_toggle.setChecked(filter_vis)


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
