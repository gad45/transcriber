"""Video player widget with playback controls."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot, QUrl
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel,
    QStyle, QSizePolicy
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget


def format_time(ms: int) -> str:
    """Format milliseconds as MM:SS."""
    seconds = ms // 1000
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


class VideoPlayer(QWidget):
    """
    Video player widget with playback controls.

    Signals:
        position_changed(int): Emitted when playback position changes (in ms)
        duration_changed(int): Emitted when video duration is known (in ms)
    """

    position_changed = Signal(int)
    duration_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._video_path: Path | None = None
        self._duration_ms: int = 0
        self._seeking = False

        self._setup_ui()
        self._setup_media_player()
        self._connect_signals()

    def _setup_ui(self):
        """Set up the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Video display
        self._video_widget = QVideoWidget()
        self._video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._video_widget.setMinimumHeight(200)
        layout.addWidget(self._video_widget, stretch=1)

        # Time slider
        slider_layout = QHBoxLayout()
        slider_layout.setContentsMargins(4, 0, 4, 0)

        self._time_label = QLabel("00:00")
        self._time_label.setFixedWidth(50)
        slider_layout.addWidget(self._time_label)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        slider_layout.addWidget(self._slider, stretch=1)

        self._duration_label = QLabel("00:00")
        self._duration_label.setFixedWidth(50)
        self._duration_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        slider_layout.addWidget(self._duration_label)

        layout.addLayout(slider_layout)

        # Playback controls
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(4, 0, 4, 4)

        self._play_btn = QPushButton()
        self._play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self._play_btn.setFixedSize(40, 32)
        controls_layout.addWidget(self._play_btn)

        self._stop_btn = QPushButton()
        self._stop_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self._stop_btn.setFixedSize(40, 32)
        controls_layout.addWidget(self._stop_btn)

        controls_layout.addStretch()

        # Volume control
        volume_icon = QLabel("🔊")
        controls_layout.addWidget(volume_icon)

        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setFixedWidth(80)
        controls_layout.addWidget(self._volume_slider)

        layout.addLayout(controls_layout)

    def _setup_media_player(self):
        """Set up the media player."""
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(0.8)

        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_widget)

    def _connect_signals(self):
        """Connect internal signals."""
        # Button clicks
        self._play_btn.clicked.connect(self._toggle_play)
        self._stop_btn.clicked.connect(self._stop)

        # Slider interactions
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._slider.sliderMoved.connect(self._on_slider_moved)

        # Volume
        self._volume_slider.valueChanged.connect(self._on_volume_changed)

        # Media player signals
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)

    def load_video(self, path: Path) -> None:
        """Load a video file."""
        self._video_path = path
        self._player.setSource(QUrl.fromLocalFile(str(path)))

    def play(self) -> None:
        """Start playback."""
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._player.play()

    def pause(self) -> None:
        """Pause playback."""
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()

    def toggle_play(self) -> None:
        """Toggle between play and pause."""
        self._toggle_play()

    def stop(self) -> None:
        """Stop playback and reset position."""
        self._stop()

    def seek(self, position_ms: int) -> None:
        """Seek to a specific position in milliseconds."""
        self._player.setPosition(position_ms)

    def seek_seconds(self, seconds: float) -> None:
        """Seek to a specific position in seconds."""
        self.seek(int(seconds * 1000))

    def get_position_ms(self) -> int:
        """Get current position in milliseconds."""
        return self._player.position()

    def get_position_seconds(self) -> float:
        """Get current position in seconds."""
        return self._player.position() / 1000.0

    def get_duration_ms(self) -> int:
        """Get video duration in milliseconds."""
        return self._duration_ms

    def get_duration_seconds(self) -> float:
        """Get video duration in seconds."""
        return self._duration_ms / 1000.0

    def is_playing(self) -> bool:
        """Check if video is currently playing."""
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def jump_forward(self, seconds: float = 5.0) -> None:
        """Jump forward by specified seconds."""
        new_pos = min(self._player.position() + int(seconds * 1000), self._duration_ms)
        self._player.setPosition(new_pos)

    def jump_backward(self, seconds: float = 5.0) -> None:
        """Jump backward by specified seconds."""
        new_pos = max(self._player.position() - int(seconds * 1000), 0)
        self._player.setPosition(new_pos)

    # Private slots

    @Slot()
    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    @Slot()
    def _stop(self):
        self._player.stop()
        self._player.setPosition(0)

    @Slot()
    def _on_slider_pressed(self):
        self._seeking = True

    @Slot()
    def _on_slider_released(self):
        self._seeking = False
        self._player.setPosition(self._slider.value())

    @Slot(int)
    def _on_slider_moved(self, value: int):
        self._time_label.setText(format_time(value))

    @Slot(int)
    def _on_volume_changed(self, value: int):
        self._audio_output.setVolume(value / 100.0)

    @Slot(int)
    def _on_position_changed(self, position: int):
        if not self._seeking:
            self._slider.setValue(position)
            self._time_label.setText(format_time(position))
        self.position_changed.emit(position)

    @Slot(int)
    def _on_duration_changed(self, duration: int):
        self._duration_ms = duration
        self._slider.setRange(0, duration)
        self._duration_label.setText(format_time(duration))
        self.duration_changed.emit(duration)

    @Slot(QMediaPlayer.PlaybackState)
    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self._play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
