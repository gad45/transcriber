"""Settings panel for screen and audio recording configuration."""

from pathlib import Path
import platform
import sys

from PySide6.QtCore import Qt, Signal, QStandardPaths
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QComboBox, QSlider, QCheckBox, QPushButton,
    QGroupBox, QFileDialog, QLineEdit, QSpinBox
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QMediaDevices

from .audio_level_meter import AudioLevelMeter
from ..models import RecordingConfig


# Crop presets: (name, resolution, aspect_ratio)
# resolution takes precedence over aspect_ratio
CROP_PRESETS = [
    ("Full Screen", None, None),
    # Fixed resolutions
    ("1920x1080 (1080p)", (1920, 1080), None),
    ("1280x720 (720p)", (1280, 720), None),
    ("1080x1920 (Vertical 1080p)", (1080, 1920), None),
    ("720x1280 (Vertical 720p)", (720, 1280), None),
    ("1080x1080 (Square)", (1080, 1080), None),
    # Aspect ratios (fit to screen height)
    ("16:9 (Fit Height)", None, (16, 9)),
    ("9:16 (Fit Height)", None, (9, 16)),
    ("4:3 (Fit Height)", None, (4, 3)),
    ("21:9 (Fit Height)", None, (21, 9)),
]

QUALITY_OPTIONS = [
    ("Low", "low"),
    ("Medium", "medium"),
    ("High", "high"),
    ("Very High", "very_high"),
]


class RecordingSettingsPanel(QWidget):
    """Settings panel for configuring screen and audio recording.

    Provides controls for:
    - Screen selection (multi-monitor support)
    - Crop mode selection (fixed resolution or aspect ratio)
    - Audio input device selection
    - Volume control with level meter
    - Output directory and quality settings

    Signals:
        settings_changed: Emitted when any setting changes
        screen_changed: Emitted when screen selection changes (index)
        crop_mode_changed: Emitted when crop mode changes (resolution, aspect_ratio)
        audio_device_changed: Emitted when audio device changes (device_id)
        audio_volume_changed: Emitted when volume changes (0.0-1.0)
        audio_enabled_changed: Emitted when microphone/input audio is enabled/disabled
        system_audio_enabled_changed: Emitted when macOS system audio capture is enabled/disabled
    """

    settings_changed = Signal()
    screen_changed = Signal(int)
    crop_mode_changed = Signal(object, object)  # (resolution, aspect_ratio)
    audio_device_changed = Signal(str)
    audio_volume_changed = Signal(float)
    audio_enabled_changed = Signal(bool)
    system_audio_enabled_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._config = RecordingConfig()
        self._updating = False  # Prevent signal loops

        self._setup_ui()
        self._populate_devices()
        self._connect_signals()

    def _setup_ui(self):
        """Set up the settings panel UI."""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # Screen settings group
        screen_group = QGroupBox("Screen")
        screen_layout = QFormLayout(screen_group)

        self._screen_combo = QComboBox()
        self._screen_combo.setToolTip("Select which screen to record")
        screen_layout.addRow("Display:", self._screen_combo)

        self._crop_combo = QComboBox()
        self._crop_combo.setToolTip("Select crop size: fixed resolution or aspect ratio")
        for name, _, _ in CROP_PRESETS:
            self._crop_combo.addItem(name)
        screen_layout.addRow("Crop:", self._crop_combo)

        layout.addWidget(screen_group)

        # Audio settings group
        audio_group = QGroupBox("Audio")
        audio_layout = QVBoxLayout(audio_group)

        self._system_audio_enabled_check: QCheckBox | None = None
        self._macos_audio_hint: QLabel | None = None

        if sys.platform == "darwin":
            self._system_audio_enabled_check = QCheckBox("Include macOS System Audio")
            self._system_audio_enabled_check.setChecked(self._config.system_audio_enabled)
            self._system_audio_enabled_check.setToolTip(
                "Captures the Mac's system output using ScreenCaptureKit on macOS 15 or later."
            )
            audio_layout.addWidget(self._system_audio_enabled_check)

        # Audio enable checkbox
        self._audio_enabled_check = QCheckBox("Include Microphone / Input Audio")
        self._audio_enabled_check.setChecked(True)
        self._audio_enabled_check.setToolTip(
            "Captures the selected microphone or other audio input device."
        )
        audio_layout.addWidget(self._audio_enabled_check)

        # Audio device selection
        device_layout = QFormLayout()
        self._audio_device_combo = QComboBox()
        self._audio_device_combo.setToolTip(
            "Select the microphone or input device to record."
        )
        device_layout.addRow("Input Device:", self._audio_device_combo)
        audio_layout.addLayout(device_layout)

        if sys.platform == "darwin":
            self._macos_audio_hint = QLabel()
            self._macos_audio_hint.setObjectName("macosAudioHint")
            self._macos_audio_hint.setWordWrap(True)
            audio_layout.addWidget(self._macos_audio_hint)

        # Volume slider
        volume_layout = QHBoxLayout()
        volume_label = QLabel("Volume:")
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(100)
        self._volume_slider.setToolTip("Adjust input volume")
        self._volume_value_label = QLabel("100%")
        self._volume_value_label.setMinimumWidth(40)
        volume_layout.addWidget(volume_label)
        volume_layout.addWidget(self._volume_slider, 1)
        volume_layout.addWidget(self._volume_value_label)
        audio_layout.addLayout(volume_layout)

        # Audio level meter
        meter_layout = QHBoxLayout()
        meter_label = QLabel("Level:")
        self._level_meter = AudioLevelMeter()
        meter_layout.addWidget(meter_label)
        meter_layout.addWidget(self._level_meter, 1)
        audio_layout.addLayout(meter_layout)

        layout.addWidget(audio_group)

        # Output settings group
        output_group = QGroupBox("Output")
        output_layout = QFormLayout(output_group)

        # Output directory
        dir_layout = QHBoxLayout()
        self._output_dir_edit = QLineEdit()
        self._output_dir_edit.setPlaceholderText("Default: Movies folder")
        self._output_dir_edit.setReadOnly(True)
        self._browse_btn = QPushButton("Browse...")
        self._browse_btn.setFixedWidth(80)
        dir_layout.addWidget(self._output_dir_edit, 1)
        dir_layout.addWidget(self._browse_btn)
        output_layout.addRow("Save to:", dir_layout)

        # Quality
        self._quality_combo = QComboBox()
        for name, _ in QUALITY_OPTIONS:
            self._quality_combo.addItem(name)
        self._quality_combo.setCurrentIndex(2)  # Default to High
        output_layout.addRow("Quality:", self._quality_combo)

        layout.addWidget(output_group)

        # Stretch at bottom
        layout.addStretch()

        # Apply styling
        self._apply_styling()

    def _apply_styling(self):
        """Apply dark theme styling."""
        style = """
            QGroupBox {
                font-weight: bold;
                border: 1px solid #444;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }
            QComboBox, QLineEdit, QSpinBox {
                background: #333;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 4px;
                min-height: 24px;
            }
            QComboBox:hover, QLineEdit:hover {
                border-color: #666;
            }
            QComboBox::drop-down {
                border: none;
                padding-right: 8px;
            }
            QPushButton {
                background: #444;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: #555;
            }
            QLabel#macosAudioHint {
                color: #aaa;
                font-size: 12px;
                line-height: 1.3em;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #333;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #666;
                width: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: #888;
            }
        """
        self.setStyleSheet(style)

    @staticmethod
    def _supports_native_system_audio() -> bool:
        """Return True when native macOS system audio capture is available."""
        if sys.platform != "darwin":
            return False

        version = platform.mac_ver()[0]
        if not version:
            return False

        parts = version.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor) >= (15, 0)

    def _populate_devices(self):
        """Populate screen and audio device lists."""
        self._updating = True

        # Screens
        self._screen_combo.clear()
        screens = QGuiApplication.screens()
        for i, screen in enumerate(screens):
            name = screen.name()
            geo = screen.geometry()
            self._screen_combo.addItem(
                f"{name} ({geo.width()}x{geo.height()})",
                i
            )

        # Audio devices
        self._audio_device_combo.clear()
        self._audio_device_combo.addItem("Default Input Device", "")

        devices = QMediaDevices.audioInputs()
        for device in devices:
            device_id = device.id().data().decode()
            self._audio_device_combo.addItem(device.description(), device_id)

        if self._macos_audio_hint is not None:
            supports_native = self._supports_native_system_audio()
            if self._system_audio_enabled_check is not None:
                self._system_audio_enabled_check.setEnabled(supports_native)
                if not supports_native:
                    self._config.system_audio_enabled = False
                    self._system_audio_enabled_check.setChecked(False)

            if supports_native:
                self._macos_audio_hint.setText(
                    "System audio capture uses the native macOS screen and audio "
                    "recording permission. If you launch via Terminal or "
                    "launch_gui.command, the permission entry usually appears under Terminal."
                )
            else:
                self._macos_audio_hint.setText(
                    "Native macOS system audio capture requires macOS 15 or later. "
                    "On older versions, use a loopback input such as BlackHole."
                )

        self._updating = False

    def _connect_signals(self):
        """Connect UI signals to handlers."""
        self._screen_combo.currentIndexChanged.connect(self._on_screen_changed)
        self._crop_combo.currentIndexChanged.connect(self._on_crop_changed)
        if self._system_audio_enabled_check is not None:
            self._system_audio_enabled_check.toggled.connect(self._on_system_audio_enabled_changed)
        self._audio_enabled_check.toggled.connect(self._on_audio_enabled_changed)
        self._audio_device_combo.currentIndexChanged.connect(self._on_audio_device_changed)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        self._browse_btn.clicked.connect(self._on_browse_clicked)
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)

    def _on_screen_changed(self, index: int):
        """Handle screen selection change."""
        if self._updating:
            return

        self._config.screen_index = index
        self.screen_changed.emit(index)
        self.settings_changed.emit()

    def _on_crop_changed(self, index: int):
        """Handle crop mode selection change."""
        if self._updating:
            return

        _, resolution, aspect_ratio = CROP_PRESETS[index]
        if resolution is None and aspect_ratio is None:
            self._config.capture_full_screen = True
            self._config.target_resolution = None
            self._config.target_aspect_ratio = None
        else:
            self._config.capture_full_screen = False
            self._config.target_resolution = resolution
            self._config.target_aspect_ratio = aspect_ratio

        self.crop_mode_changed.emit(resolution, aspect_ratio)
        self.settings_changed.emit()

    def _on_system_audio_enabled_changed(self, enabled: bool):
        """Handle macOS system audio toggle."""
        if self._updating:
            return

        self._config.system_audio_enabled = enabled
        self.system_audio_enabled_changed.emit(enabled)
        self.settings_changed.emit()

    def _on_audio_enabled_changed(self, enabled: bool):
        """Handle audio enable toggle."""
        if self._updating:
            return

        self._config.audio_enabled = enabled
        self._audio_device_combo.setEnabled(enabled)
        self._volume_slider.setEnabled(enabled)
        self._level_meter.setEnabled(enabled)

        if not enabled:
            self._level_meter.reset()

        self.audio_enabled_changed.emit(enabled)
        self.settings_changed.emit()

    def _on_audio_device_changed(self, index: int):
        """Handle audio device selection change."""
        if self._updating:
            return

        device_id = self._audio_device_combo.currentData() or ""
        self._config.audio_device_id = device_id
        self.audio_device_changed.emit(device_id)
        self.settings_changed.emit()

    def _on_volume_changed(self, value: int):
        """Handle volume slider change."""
        if self._updating:
            return

        volume = value / 100.0
        self._config.audio_volume = volume
        self._volume_value_label.setText(f"{value}%")
        self.audio_volume_changed.emit(volume)
        self.settings_changed.emit()

    def _on_browse_clicked(self):
        """Handle output directory browse button."""
        default_dir = self._config.output_directory or QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.MoviesLocation
        )

        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Output Directory",
            default_dir,
            QFileDialog.Option.ShowDirsOnly
        )

        if directory:
            self._config.output_directory = directory
            self._output_dir_edit.setText(directory)
            self.settings_changed.emit()

    def _on_quality_changed(self, index: int):
        """Handle quality selection change."""
        if self._updating:
            return

        _, quality = QUALITY_OPTIONS[index]
        self._config.video_quality = quality
        self.settings_changed.emit()

    def get_config(self) -> RecordingConfig:
        """Get the current recording configuration."""
        return self._config.copy()

    def set_config(self, config: RecordingConfig):
        """Set the recording configuration."""
        self._updating = True
        self._config = config.copy()

        # Update UI
        self._screen_combo.setCurrentIndex(config.screen_index)

        # Find crop preset index (resolution takes precedence)
        found = False
        for i, (_, resolution, aspect_ratio) in enumerate(CROP_PRESETS):
            if config.target_resolution is not None:
                # Match by resolution
                if resolution == config.target_resolution:
                    self._crop_combo.setCurrentIndex(i)
                    found = True
                    break
            elif config.target_aspect_ratio is not None:
                # Match by aspect ratio (only if no resolution set)
                if resolution is None and aspect_ratio == config.target_aspect_ratio:
                    self._crop_combo.setCurrentIndex(i)
                    found = True
                    break
        if not found:
            self._crop_combo.setCurrentIndex(0)  # Full screen

        if self._system_audio_enabled_check is not None:
            self._system_audio_enabled_check.setChecked(
                config.system_audio_enabled and self._supports_native_system_audio()
            )
            self._config.system_audio_enabled = self._system_audio_enabled_check.isChecked()
        self._audio_enabled_check.setChecked(config.audio_enabled)

        # Find audio device index
        for i in range(self._audio_device_combo.count()):
            if self._audio_device_combo.itemData(i) == config.audio_device_id:
                self._audio_device_combo.setCurrentIndex(i)
                break

        self._volume_slider.setValue(int(config.audio_volume * 100))

        if config.output_directory:
            self._output_dir_edit.setText(config.output_directory)

        # Find quality index
        for i, (_, quality) in enumerate(QUALITY_OPTIONS):
            if quality == config.video_quality:
                self._quality_combo.setCurrentIndex(i)
                break

        self._updating = False

    def set_audio_level(self, level: float):
        """Update the audio level meter."""
        self._level_meter.set_level(level)

    def refresh_devices(self):
        """Refresh the device lists."""
        self._populate_devices()

    @property
    def level_meter(self) -> AudioLevelMeter:
        """Get the audio level meter widget."""
        return self._level_meter
