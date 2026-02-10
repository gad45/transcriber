"""Main recorder tab widget combining all recording components."""

import threading
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QPushButton, QLabel, QMessageBox,
    QProgressDialog
)
from PySide6.QtGui import QGuiApplication

from .recording_controller import RecordingController, RecordingState
from .recording_preview import RecordingPreview
from .recording_settings import RecordingSettingsPanel
from .ffmpeg_worker import FFmpegCropWorker
from ..models import RecordingConfig
from ...encoder import get_encoder_args


class RecorderTab(QWidget):
    """Main recorder tab with preview, settings, and controls.

    Provides a complete screen and audio recording interface with:
    - Live preview of the screen being captured
    - Draggable crop overlay for aspect ratio selection
    - Audio device selection and volume control
    - Record/Stop/Pause controls
    - Recording timer and status

    Signals:
        recording_completed: Emitted when a recording is ready (path)
        open_in_editor_requested: Emitted when user wants to edit recording (path)
    """

    recording_completed = Signal(Path)
    open_in_editor_requested = Signal(Path)
    _crop_result_ready = Signal(bool, Path, str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._controller = RecordingController()
        self._recording_start_time: datetime | None = None
        self._timer_update = QTimer(self)
        self._crop_thread: threading.Thread | None = None
        self._crop_worker: FFmpegCropWorker | None = None
        self._crop_progress: QProgressDialog | None = None
        self._crop_auto_open = False

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        """Set up the recorder tab UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
        self._toolbar = QToolBar()
        self._toolbar.setMovable(False)
        self._setup_toolbar()
        layout.addWidget(self._toolbar)

        # Main content splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Preview (left side, larger)
        self._preview = RecordingPreview()
        splitter.addWidget(self._preview)

        # Settings panel (right side)
        self._settings_panel = RecordingSettingsPanel()
        self._settings_panel.setMinimumWidth(280)
        self._settings_panel.setMaximumWidth(350)
        splitter.addWidget(self._settings_panel)

        splitter.setSizes([700, 300])
        layout.addWidget(splitter, 1)

        # Status bar
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(8, 4, 8, 4)

        self._status_label = QLabel("Ready to record")
        status_layout.addWidget(self._status_label)

        status_layout.addStretch()

        self._timer_label = QLabel("00:00:00")
        self._timer_label.setStyleSheet("font-family: monospace; font-size: 14px;")
        status_layout.addWidget(self._timer_label)

        status_widget = QWidget()
        status_widget.setLayout(status_layout)
        status_widget.setStyleSheet("background: #2a2a2a; border-top: 1px solid #444;")
        layout.addWidget(status_widget)

        # Connect preview to capture session
        self._preview.set_capture_session(self._controller.get_video_sink())

        # Initialize preview with screen size
        screens = QGuiApplication.screens()
        if screens:
            screen_w, screen_h = RecordingController.get_screen_pixel_size(screens[0])
            self._preview.set_screen_size(screen_w, screen_h)

        # Start preview immediately so users can see what they'll record
        self._controller.start_preview()

    def _setup_toolbar(self):
        """Set up the toolbar with recording controls."""
        # Record button
        self._record_btn = QPushButton("Record")
        self._record_btn.setStyleSheet("""
            QPushButton {
                background: #c62828;
                color: white;
                font-weight: bold;
                padding: 8px 20px;
                border-radius: 4px;
                border: none;
            }
            QPushButton:hover {
                background: #d32f2f;
            }
            QPushButton:disabled {
                background: #666;
            }
        """)
        self._toolbar.addWidget(self._record_btn)

        # Stop button
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet("""
            QPushButton {
                background: #444;
                color: white;
                padding: 8px 20px;
                border-radius: 4px;
                border: none;
            }
            QPushButton:hover {
                background: #555;
            }
            QPushButton:disabled {
                background: #333;
                color: #666;
            }
        """)
        self._toolbar.addWidget(self._stop_btn)

        # Pause button
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setEnabled(False)
        self._pause_btn.setCheckable(True)
        self._pause_btn.setStyleSheet("""
            QPushButton {
                background: #444;
                color: white;
                padding: 8px 20px;
                border-radius: 4px;
                border: none;
            }
            QPushButton:hover {
                background: #555;
            }
            QPushButton:checked {
                background: #f57c00;
            }
            QPushButton:disabled {
                background: #333;
                color: #666;
            }
        """)
        self._toolbar.addWidget(self._pause_btn)

        self._toolbar.addSeparator()

        # Refresh devices button
        self._refresh_btn = QPushButton("Refresh Devices")
        self._refresh_btn.setStyleSheet("""
            QPushButton {
                background: #444;
                padding: 8px 12px;
                border-radius: 4px;
                border: none;
            }
            QPushButton:hover {
                background: #555;
            }
        """)
        self._toolbar.addWidget(self._refresh_btn)

    def _connect_signals(self):
        """Connect all signals."""
        # Toolbar buttons
        self._record_btn.clicked.connect(self._on_record_clicked)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        self._pause_btn.toggled.connect(self._on_pause_toggled)
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)

        # Settings panel
        self._settings_panel.screen_changed.connect(self._on_screen_changed)
        self._settings_panel.crop_mode_changed.connect(self._on_crop_mode_changed)
        self._settings_panel.audio_device_changed.connect(self._on_audio_device_changed)
        self._settings_panel.audio_volume_changed.connect(self._on_audio_volume_changed)
        self._settings_panel.audio_enabled_changed.connect(self._on_audio_enabled_changed)

        # Preview
        self._preview.crop_offset_changed.connect(self._on_crop_offset_changed)

        # Controller
        self._controller.recording_started.connect(self._on_recording_started)
        self._controller.recording_stopped.connect(self._on_recording_stopped)
        self._controller.recording_error.connect(self._on_recording_error)
        self._controller.recording_warning.connect(self._on_recording_warning)
        self._controller.duration_changed.connect(self._on_duration_changed)
        self._controller.state_changed.connect(self._on_state_changed)
        self._controller.audio_level_changed.connect(self._settings_panel.set_audio_level)
        self._controller.permission_status_changed.connect(self._on_permission_changed)

        # Timer for updating display
        self._timer_update.timeout.connect(self._update_timer_display)
        self._crop_result_ready.connect(self._on_crop_finished)

    def _on_record_clicked(self):
        """Handle record button click."""
        # Apply current settings
        config = self._settings_panel.get_config()
        self._controller.set_config(config)

        if not self._controller.start_recording():
            QMessageBox.warning(
                self,
                "Recording Error",
                "Failed to start recording. Check screen capture permissions."
            )

    def _on_stop_clicked(self):
        """Handle stop button click."""
        self._stop_btn.setEnabled(False)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setChecked(False)
        self._status_label.setText("Stopping...")
        self._timer_update.stop()
        self._controller.stop_recording()

    def _on_pause_toggled(self, checked: bool):
        """Handle pause button toggle."""
        if checked:
            self._controller.pause_recording()
        else:
            self._controller.resume_recording()

    def _on_refresh_clicked(self):
        """Handle refresh devices button click."""
        self._settings_panel.refresh_devices()

    def _on_screen_changed(self, index: int):
        """Handle screen selection change."""
        self._controller.set_screen(index)
        # Update preview with screen size for resolution scaling
        screens = QGuiApplication.screens()
        if index < len(screens):
            screen_w, screen_h = RecordingController.get_screen_pixel_size(screens[index])
            self._preview.set_screen_size(screen_w, screen_h)

    def _on_crop_mode_changed(self, resolution, aspect_ratio):
        """Handle crop mode change (resolution or aspect ratio)."""
        self._controller.set_crop_mode(resolution, aspect_ratio)
        self._preview.set_crop_mode(resolution, aspect_ratio)

    def _on_audio_device_changed(self, device_id: str):
        """Handle audio device change."""
        self._controller.set_audio_device(device_id)

    def _on_audio_volume_changed(self, volume: float):
        """Handle volume change."""
        self._controller.set_audio_volume(volume)

    def _on_audio_enabled_changed(self, enabled: bool):
        """Handle audio enable/disable."""
        self._controller.set_audio_enabled(enabled)

    def _on_crop_offset_changed(self, x: float, y: float):
        """Handle crop region being moved."""
        self._controller.set_crop_offset(x, y)

    def _on_permission_changed(self, granted: bool):
        """Handle microphone permission result."""
        if granted:
            self._status_label.setText("Microphone access granted")
        else:
            self._status_label.setText("Microphone access denied - check System Settings")

    def _on_recording_started(self):
        """Handle recording started."""
        self._recording_start_time = datetime.now()
        self._record_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._pause_btn.setEnabled(True)
        self._settings_panel.setEnabled(False)
        self._status_label.setText("Recording...")
        self._timer_update.start(100)

    def _on_recording_stopped(self, output_path: Path, needs_crop: bool):
        """Handle recording stopped."""
        self._timer_update.stop()
        config = self._controller.get_last_recording_config()
        auto_open = bool(config and not config.capture_full_screen)

        if needs_crop:
            self._set_ui_processing()
            self._process_crop(output_path, config, auto_open=auto_open)
        else:
            self._set_ui_idle()
            self._show_completion_dialog(output_path, auto_open=auto_open)

    def _on_recording_error(self, error: str):
        """Handle recording error."""
        self._timer_update.stop()
        self._set_ui_idle()

        QMessageBox.critical(self, "Recording Error", error)

    def _on_recording_warning(self, message: str):
        """Handle recording warning (non-fatal)."""
        QMessageBox.warning(self, "Recording Warning", message)

    def _on_duration_changed(self, duration_ms: int):
        """Handle duration update."""
        self._update_timer_display()

    def _on_state_changed(self, state: RecordingState):
        """Handle state change."""
        if state == RecordingState.PAUSED:
            self._status_label.setText("Paused")
        elif state == RecordingState.RECORDING:
            self._status_label.setText("Recording...")
        elif state == RecordingState.PROCESSING:
            self._status_label.setText("Processing...")

    def _update_timer_display(self):
        """Update the timer display."""
        if self._recording_start_time:
            elapsed = datetime.now() - self._recording_start_time
            hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            self._timer_label.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def _process_crop(
        self,
        input_path: Path,
        config: RecordingConfig | None = None,
        auto_open: bool = False,
    ):
        """Process the recording with FFmpeg crop filter.

        The raw recording in the 'raw' subdirectory is NEVER deleted.
        The cropped version is saved to the parent directory.
        """
        if config is None:
            config = self._settings_panel.get_config()

        # Get screen dimensions for crop calculation
        screens = QGuiApplication.screens()
        if config.screen_index < len(screens):
            screen = screens[config.screen_index]
            screen_width, screen_height = RecordingController.get_screen_pixel_size(screen)
        else:
            # Fallback
            screen_width = 1920
            screen_height = 1080

        crop_filter = config.to_ffmpeg_crop_filter(screen_width, screen_height)
        if not crop_filter:
            self._set_ui_idle()
            self._show_completion_dialog(input_path, auto_open=auto_open)
            return

        # Cropped file goes to parent directory (raw stays in raw/)
        # e.g., raw/recording_123.mp4 -> Recordings/recording_123.mp4
        output_path = input_path.parent.parent / input_path.name

        self._crop_auto_open = auto_open
        self._start_crop_worker(input_path, output_path, crop_filter)

    def _start_crop_worker(self, input_path: Path, output_path: Path, crop_filter: str) -> None:
        """Start the FFmpeg crop worker in a background thread."""
        if self._crop_thread and self._crop_thread.is_alive():
            return

        worker = FFmpegCropWorker(
            input_path=input_path,
            output_path=output_path,
            crop_filter=crop_filter,
            encoder_args=get_encoder_args(),
        )
        self._crop_worker = worker

        def run_crop() -> None:
            success, result_path, message = worker.run()
            self._crop_result_ready.emit(success, result_path, message)

        self._crop_thread = threading.Thread(target=run_crop, name="ffmpeg-crop", daemon=True)
        self._crop_thread.start()

        self._crop_progress = QProgressDialog("Cropping video...", "Cancel", 0, 0, self)
        self._crop_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._crop_progress.setAutoClose(True)
        self._crop_progress.setAutoReset(False)
        self._crop_progress.canceled.connect(self._on_crop_canceled)
        self._crop_progress.show()

    def _on_crop_canceled(self) -> None:
        if self._crop_worker:
            self._crop_worker.cancel()
        if self._crop_progress:
            self._crop_progress.setLabelText("Canceling...")

    def _on_crop_finished(self, success: bool, result_path: Path, message: str) -> None:
        if self._crop_progress:
            self._crop_progress.close()
            self._crop_progress = None

        self._crop_worker = None
        self._crop_thread = None

        self._set_ui_idle()

        if not success and message:
            QMessageBox.warning(self, "Crop Warning", message)

        self._show_completion_dialog(result_path, auto_open=self._crop_auto_open)

    def _show_completion_dialog(self, output_path: Path, auto_open: bool = False):
        """Show recording completion dialog or auto-open."""
        self.recording_completed.emit(output_path)

        if auto_open:
            self.open_in_editor_requested.emit(output_path)
            return

        reply = QMessageBox.question(
            self,
            "Recording Complete",
            f"Recording saved to:\n{output_path}\n\nOpen in editor?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.open_in_editor_requested.emit(output_path)

    def _set_ui_processing(self) -> None:
        self._record_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setChecked(False)
        self._settings_panel.setEnabled(False)
        self._status_label.setText("Processing...")

    def _set_ui_idle(self) -> None:
        self._record_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setChecked(False)
        self._settings_panel.setEnabled(True)
        self._status_label.setText("Ready to record")

    def get_config(self) -> RecordingConfig:
        """Get current recording configuration."""
        return self._settings_panel.get_config()

    def set_config(self, config: RecordingConfig):
        """Set recording configuration."""
        self._settings_panel.set_config(config)
        self._controller.set_config(config)
        if config.capture_full_screen:
            self._preview.set_crop_mode(None, None)
        else:
            self._preview.set_crop_mode(config.target_resolution, config.target_aspect_ratio)

    def showEvent(self, event):
        """Handle widget becoming visible."""
        super().showEvent(event)
        # Restart preview when tab becomes visible
        if not self._controller.is_recording:
            self._controller.start_preview()

    def hideEvent(self, event):
        """Handle widget being hidden."""
        super().hideEvent(event)
        # Stop preview when tab is hidden (to save resources)
        if not self._controller.is_recording:
            self._controller.stop_preview()
