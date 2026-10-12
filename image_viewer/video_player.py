from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel,
    QStyle, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

VIDEO_EXTENSIONS = {
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv",
    ".webm", ".m4v", ".3gp", ".mpg", ".mpeg", ".ts",
}


def is_video_file(path: str) -> bool:
    import os
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
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._audio.setVolume(0.5)

        self._video_widget = QVideoWidget(self)
        self._player.setVideoOutput(self._video_widget)
        self._video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

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
        layout.addWidget(self._video_widget, 1)
        layout.addLayout(controls)

        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.mediaStatusChanged.connect(self._on_media_status)

        self._slider_held = False

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
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()
        self._play_btn.setText("  ⏸  ")
        return True

    def stop(self):
        self._player.stop()
        self._player.setSource(QUrl())

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _seek(self, pos: int):
        self._player.setPosition(pos)

    def _on_slider_pressed(self):
        self._slider_held = True

    def _on_slider_released(self):
        self._slider_held = False

    def _set_volume(self, val: int):
        self._audio.setVolume(val / 100.0)

    def _toggle_mute(self):
        muted = not self._audio.isMuted()
        self._audio.setMuted(muted)
        self._mute_btn.setText(" 🔇 " if muted else " 🔊 ")

    def _on_position(self, pos: int):
        if not self._slider_held:
            self._slider.setValue(pos)
        dur = self._player.duration()
        self._time_label.setText(f"{_fmt_time(pos)} / {_fmt_time(dur)}")

    def _on_duration(self, dur: int):
        self._slider.setRange(0, dur)

    def _on_state(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_btn.setText("  ⏸  ")
        else:
            self._play_btn.setText("  ▶  ")

    def _on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.playback_finished.emit()
