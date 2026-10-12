import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer, QUrl
from PySide6.QtGui import QImage, QPixmap

VIDEO_EXTENSIONS = {
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv",
    ".webm", ".m4v", ".3gp", ".mpg", ".mpeg", ".ts",
    ".vob", ".ogv", ".mts", ".m2ts", ".divx", ".xvid",
    ".f4v", ".rm", ".rmvb", ".asf", ".m2v", ".mxf",
    ".dv", ".gif", ".apng",
}


def is_video_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def _fmt_time(ms: int) -> str:
    s = ms // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class VideoPlayer(QWidget):
    playback_finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cap = None
        self._audio_player = None
        self._audio_output = None
        self._path: str | None = None
        self._playing = False
        self._fps = 30.0
        self._frame_count = 0
        self._duration_ms = 0
        self._current_frame = 0
        self._slider_held = False
        self._volume = 50
        self._muted = False

        self._display = QLabel()
        self._display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._display.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._display.setStyleSheet("background-color: #000;")
        self._display.setScaledContents(False)

        self._play_btn = QPushButton("  ▶  ")
        self._play_btn.setFixedWidth(50)
        self._play_btn.clicked.connect(self._toggle_play)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.sliderMoved.connect(self._seek)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)

        self._time_label = QLabel("0:00 / 0:00")
        self._time_label.setFixedWidth(120)
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(50)
        self._vol_slider.setFixedWidth(80)
        self._vol_slider.valueChanged.connect(self._set_volume)

        self._mute_btn = QPushButton(" 🔊 ")
        self._mute_btn.setFixedWidth(40)
        self._mute_btn.clicked.connect(self._toggle_mute)

        controls = QHBoxLayout()
        controls.setContentsMargins(8, 0, 8, 4)
        controls.addWidget(self._play_btn)
        controls.addWidget(self._slider)
        controls.addWidget(self._time_label)
        controls.addWidget(self._mute_btn)
        controls.addWidget(self._vol_slider)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._display, 1)
        layout.addLayout(controls)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next_frame)

        self.setStyleSheet("""
            QWidget { background-color: #000; }
            QPushButton {
                background-color: #333; color: #eee;
                border: none; border-radius: 4px; padding: 4px;
                font-size: 14px;
            }
            QPushButton:hover { background-color: #555; }
            QSlider::groove:horizontal {
                height: 6px; background: #555; border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 14px; height: 14px; margin: -4px 0;
                background: #0078d4; border-radius: 7px;
            }
            QSlider::sub-page:horizontal { background: #0078d4; border-radius: 3px; }
            QLabel { color: #aaa; font-size: 12px; }
        """)

    def load(self, path: str) -> bool:
        self.stop()
        try:
            import cv2
            self._cap = cv2.VideoCapture(path)
            if not self._cap.isOpened():
                return self._fallback_load(path)
            self._path = path
            self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
            self._frame_count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self._duration_ms = int((self._frame_count / self._fps) * 1000) if self._fps > 0 else 0
            self._current_frame = 0
            self._slider.setRange(0, self._duration_ms)
            self._show_frame()
            self._start_audio(path)
            self._playing = True
            self._play_btn.setText("  ⏸  ")
            self._timer.start(max(1, int(1000 / self._fps)))
            return True
        except ImportError:
            return self._fallback_load(path)

    def _fallback_load(self, path: str) -> bool:
        """Fall back to QMediaPlayer if OpenCV is not available."""
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PySide6.QtMultimediaWidgets import QVideoWidget
            if not hasattr(self, "_qt_video_widget"):
                self._qt_video_widget = QVideoWidget(self)
                self._qt_player = QMediaPlayer(self)
                self._qt_audio = QAudioOutput(self)
                self._qt_player.setAudioOutput(self._qt_audio)
                self._qt_player.setVideoOutput(self._qt_video_widget)
                self._qt_player.positionChanged.connect(self._on_qt_position)
                self._qt_player.durationChanged.connect(self._on_qt_duration)
                self._qt_player.playbackStateChanged.connect(self._on_qt_state)
                self._qt_player.mediaStatusChanged.connect(self._on_qt_media_status)
                self.layout().insertWidget(0, self._qt_video_widget, 1)
            self._display.hide()
            self._qt_video_widget.show()
            self._qt_audio.setVolume(self._volume / 100.0)
            self._qt_player.setSource(QUrl.fromLocalFile(path))
            self._qt_player.play()
            self._playing = True
            self._play_btn.setText("  ⏸  ")
            self._path = path
            return True
        except ImportError:
            return False

    def _on_qt_position(self, pos):
        if not self._slider_held:
            self._slider.setValue(pos)
        dur = self._qt_player.duration() if hasattr(self, "_qt_player") else 0
        self._time_label.setText(f"{_fmt_time(pos)} / {_fmt_time(dur)}")

    def _on_qt_duration(self, dur):
        self._slider.setRange(0, dur)
        self._duration_ms = dur

    def _on_qt_state(self, state):
        from PySide6.QtMultimedia import QMediaPlayer
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_btn.setText("  ⏸  ")
            self._playing = True
        else:
            self._play_btn.setText("  ▶  ")
            self._playing = False

    def _on_qt_media_status(self, status):
        from PySide6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.playback_finished.emit()

    def _start_audio(self, path: str):
        """Start audio playback via QMediaPlayer alongside OpenCV video."""
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
            self._audio_player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._audio_player.setAudioOutput(self._audio_output)
            vol = 0.0 if self._muted else self._volume / 100.0
            self._audio_output.setVolume(vol)
            self._audio_player.setSource(QUrl.fromLocalFile(path))
            self._audio_player.play()
        except Exception:
            self._audio_player = None

    def _show_frame(self):
        if not self._cap:
            return
        import cv2
        ret, frame = self._cap.read()
        if not ret:
            return
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame.shape
        img = QImage(frame.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(img)
        disp_size = self._display.size()
        scaled = pixmap.scaled(
            disp_size, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._display.setPixmap(scaled)

    def _next_frame(self):
        if not self._cap or not self._playing:
            return
        import cv2
        ret, frame = self._cap.read()
        if not ret:
            self._playing = False
            self._timer.stop()
            self._play_btn.setText("  ▶  ")
            self.playback_finished.emit()
            return
        self._current_frame += 1
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame.shape
        img = QImage(frame.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(img)
        disp_size = self._display.size()
        scaled = pixmap.scaled(
            disp_size, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._display.setPixmap(scaled)
        pos_ms = int((self._current_frame / self._fps) * 1000) if self._fps > 0 else 0
        if not self._slider_held:
            self._slider.setValue(pos_ms)
        self._time_label.setText(f"{_fmt_time(pos_ms)} / {_fmt_time(self._duration_ms)}")

    def stop(self):
        self._timer.stop()
        self._playing = False
        if self._cap:
            self._cap.release()
            self._cap = None
        if self._audio_player:
            self._audio_player.stop()
            self._audio_player = None
            self._audio_output = None
        if hasattr(self, "_qt_player"):
            self._qt_player.stop()
            self._qt_player.setSource(QUrl())
        self._path = None

    def _toggle_play(self):
        if hasattr(self, "_qt_player") and self._qt_video_widget.isVisible():
            from PySide6.QtMultimedia import QMediaPlayer
            if self._qt_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._qt_player.pause()
            else:
                self._qt_player.play()
            return
        if self._playing:
            self._playing = False
            self._timer.stop()
            self._play_btn.setText("  ▶  ")
            if self._audio_player:
                self._audio_player.pause()
        else:
            if self._cap and self._current_frame >= self._frame_count - 1:
                import cv2
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self._current_frame = 0
                if self._audio_player:
                    self._audio_player.setPosition(0)
            self._playing = True
            self._play_btn.setText("  ⏸  ")
            self._timer.start(max(1, int(1000 / self._fps)))
            if self._audio_player:
                self._audio_player.play()

    def _seek(self, pos_ms: int):
        if hasattr(self, "_qt_player") and self._qt_video_widget.isVisible():
            self._qt_player.setPosition(pos_ms)
            return
        if not self._cap:
            return
        import cv2
        frame_no = int((pos_ms / 1000.0) * self._fps)
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        self._current_frame = frame_no
        self._show_frame()
        if self._audio_player:
            self._audio_player.setPosition(pos_ms)

    def _on_slider_pressed(self):
        self._slider_held = True

    def _on_slider_released(self):
        self._slider_held = False
        self._seek(self._slider.value())

    def _set_volume(self, val: int):
        self._volume = val
        if self._audio_output and not self._muted:
            self._audio_output.setVolume(val / 100.0)
        if hasattr(self, "_qt_audio"):
            self._qt_audio.setVolume(val / 100.0)

    def _toggle_mute(self):
        self._muted = not self._muted
        self._mute_btn.setText(" 🔇 " if self._muted else " 🔊 ")
        if self._audio_output:
            self._audio_output.setVolume(0.0 if self._muted else self._volume / 100.0)
        if hasattr(self, "_qt_audio"):
            self._qt_audio.setMuted(self._muted)
