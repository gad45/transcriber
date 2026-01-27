"""Video player widget with playback controls and crop/pan support."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot, QUrl, QRectF, QSizeF
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel,
    QStyle, QSizePolicy, QGraphicsScene, QGraphicsView, QGraphicsRectItem
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtGui import QBrush, QColor, QPen, QPainter

from .models import CropConfig


def format_time(ms: int) -> str:
    """Format milliseconds as MM:SS."""
    seconds = ms // 1000
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


class VideoView(QGraphicsView):
    """Custom graphics view for video display with crop overlay support."""

    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor(0, 0, 0)))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)

    def resizeEvent(self, event):
        """Fit the scene to the view when resized."""
        super().resizeEvent(event)
        if self.scene():
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


class VideoPlayer(QWidget):
    """
    Video player widget with playback controls and crop/pan support.

    Uses QGraphicsVideoItem for video display, allowing crop preview
    via viewport transforms.

    Signals:
        position_changed(int): Emitted when playback position changes (in ms)
        duration_changed(int): Emitted when video duration is known (in ms)
        crop_changed(CropConfig): Emitted when crop settings change
    """

    position_changed = Signal(int)
    duration_changed = Signal(int)
    crop_changed = Signal(object)  # CropConfig

    def __init__(self, parent=None):
        super().__init__(parent)

        self._video_path: Path | None = None
        self._duration_ms: int = 0
        self._seeking = False

        # Video dimensions (set when video loads)
        self._video_width: int = 1920
        self._video_height: int = 1080

        # Crop configuration
        self._crop_config = CropConfig()
        self._crop_mode = False

        self._setup_ui()
        self._setup_media_player()
        self._connect_signals()

    def _setup_ui(self):
        """Set up the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Graphics scene and view for video
        self._scene = QGraphicsScene()
        self._view = VideoView(self._scene)
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._view.setMinimumHeight(200)

        # Video item
        self._video_item = QGraphicsVideoItem()
        self._scene.addItem(self._video_item)

        # Crop overlay (semi-transparent dark areas outside crop region)
        self._crop_overlay_top = QGraphicsRectItem()
        self._crop_overlay_bottom = QGraphicsRectItem()
        self._crop_overlay_left = QGraphicsRectItem()
        self._crop_overlay_right = QGraphicsRectItem()
        self._crop_border = QGraphicsRectItem()

        overlay_brush = QBrush(QColor(0, 0, 0, 160))
        border_pen = QPen(QColor(33, 150, 243), 2)  # Blue border

        for overlay in [self._crop_overlay_top, self._crop_overlay_bottom,
                        self._crop_overlay_left, self._crop_overlay_right]:
            overlay.setBrush(overlay_brush)
            overlay.setPen(QPen(Qt.PenStyle.NoPen))
            overlay.setZValue(10)
            overlay.setVisible(False)
            self._scene.addItem(overlay)

        self._crop_border.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._crop_border.setPen(border_pen)
        self._crop_border.setZValue(11)
        self._crop_border.setVisible(False)
        self._scene.addItem(self._crop_border)

        layout.addWidget(self._view, stretch=1)

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
        volume_icon = QLabel("Vol")
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
        self._player.setVideoOutput(self._video_item)

        # Connect to native size changed to update scene rect
        self._video_item.nativeSizeChanged.connect(self._on_native_size_changed)

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

    @Slot(QSizeF)
    def _on_native_size_changed(self, size: QSizeF):
        """Handle video size change when video loads."""
        if size.width() > 0 and size.height() > 0:
            self._video_width = int(size.width())
            self._video_height = int(size.height())

            # Set video item size
            self._video_item.setSize(size)

            # Update scene rect to match video
            self._scene.setSceneRect(0, 0, size.width(), size.height())

            # Fit view to scene
            self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

            # Update crop overlay if visible
            if self._crop_mode:
                self._update_crop_overlay()

    def _update_crop_overlay(self):
        """Update the crop overlay rectangles based on current crop config."""
        if not self._crop_mode:
            return

        video_w = self._video_width
        video_h = self._video_height

        # Get crop rectangle
        crop_x, crop_y, crop_w, crop_h = self._crop_config.get_crop_rect(video_w, video_h)

        # Update overlay rectangles (dark areas outside crop)
        # Top overlay
        self._crop_overlay_top.setRect(0, 0, video_w, crop_y)
        # Bottom overlay
        self._crop_overlay_bottom.setRect(0, crop_y + crop_h, video_w, video_h - crop_y - crop_h)
        # Left overlay
        self._crop_overlay_left.setRect(0, crop_y, crop_x, crop_h)
        # Right overlay
        self._crop_overlay_right.setRect(crop_x + crop_w, crop_y, video_w - crop_x - crop_w, crop_h)

        # Update border rectangle
        self._crop_border.setRect(crop_x, crop_y, crop_w, crop_h)

    # Public API

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

    # Crop API

    def get_video_dimensions(self) -> tuple[int, int]:
        """Get the video dimensions (width, height)."""
        return self._video_width, self._video_height

    def set_crop_mode(self, enabled: bool) -> None:
        """Enable or disable crop editing mode."""
        self._crop_mode = enabled

        # Show/hide crop overlay
        for overlay in [self._crop_overlay_top, self._crop_overlay_bottom,
                        self._crop_overlay_left, self._crop_overlay_right]:
            overlay.setVisible(enabled)
        self._crop_border.setVisible(enabled)

        if enabled:
            self._update_crop_overlay()
        else:
            # When exiting crop mode, zoom to show only cropped region
            self._apply_crop_view()

    def is_crop_mode(self) -> bool:
        """Check if crop mode is active."""
        return self._crop_mode

    def set_crop_config(self, config: CropConfig) -> None:
        """Set the crop configuration."""
        self._crop_config = config
        if self._crop_mode:
            self._update_crop_overlay()
        else:
            self._apply_crop_view()
        self.crop_changed.emit(config)

    def get_crop_config(self) -> CropConfig:
        """Get the current crop configuration."""
        return self._crop_config

    def _apply_crop_view(self):
        """Apply crop by zooming the view to show only the cropped region."""
        if self._crop_config.is_default:
            # Show full video
            self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        else:
            # Zoom to crop region
            crop_x, crop_y, crop_w, crop_h = self._crop_config.get_crop_rect(
                self._video_width, self._video_height
            )
            crop_rect = QRectF(crop_x, crop_y, crop_w, crop_h)
            self._view.fitInView(crop_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def adjust_crop_size(self, width_delta: float, height_delta: float) -> None:
        """Adjust crop size by delta values (normalized)."""
        new_width = max(0.1, min(1.0, self._crop_config.width + width_delta))
        new_height = max(0.1, min(1.0, self._crop_config.height + height_delta))
        self._crop_config.width = new_width
        self._crop_config.height = new_height

        # Clamp pan to valid range
        self._clamp_pan()

        if self._crop_mode:
            self._update_crop_overlay()
        self.crop_changed.emit(self._crop_config)

    def adjust_pan(self, pan_x_delta: float, pan_y_delta: float) -> None:
        """Adjust pan offset by delta values."""
        self._crop_config.pan_x += pan_x_delta
        self._crop_config.pan_y += pan_y_delta
        self._clamp_pan()

        if self._crop_mode:
            self._update_crop_overlay()
        else:
            self._apply_crop_view()
        self.crop_changed.emit(self._crop_config)

    def _clamp_pan(self):
        """Clamp pan values to valid range (-1 to 1)."""
        self._crop_config.pan_x = max(-1.0, min(1.0, self._crop_config.pan_x))
        self._crop_config.pan_y = max(-1.0, min(1.0, self._crop_config.pan_y))

    def reset_crop(self) -> None:
        """Reset crop to default (full frame)."""
        self._crop_config = CropConfig()
        if self._crop_mode:
            self._update_crop_overlay()
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.crop_changed.emit(self._crop_config)

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
