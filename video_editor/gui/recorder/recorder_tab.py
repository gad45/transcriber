"""Main recorder tab widget combining all recording components."""

import json
import threading
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QPushButton, QLabel, QMessageBox,
    QProgressDialog, QStackedWidget
)
from PySide6.QtGui import QGuiApplication

from .recording_controller import RecordingController, RecordingState
from .recording_preview import RecordingPreview
from .recording_settings import RecordingSettingsPanel
from .teleprompter import TeleprompterView
from .ffmpeg_worker import FFmpegCropWorker
from .macos_permissions import has_screen_capture_access, is_macos
from ..models import RecordingConfig
from ...encoder import get_encoder_args


def cropped_recording_output_path(input_path: Path) -> Path:
    """Return the cropped output path for a raw recorder capture."""
    if input_path.parent.name == "raw":
        return input_path.parent.parent / input_path.name
    return input_path.with_name(f"{input_path.stem}_cropped{input_path.suffix}")


def is_raw_recording_backup_path(input_path: Path) -> bool:
    """Return True when the path is the recorder's full-screen raw backup."""
    return input_path.parent.name == "raw"


def select_recording_crop_config(
    recording_config: RecordingConfig | None,
    ui_config: RecordingConfig | None,
) -> RecordingConfig | None:
    """Pick the best available crop config for post-recording processing."""
    if recording_config and recording_config.needs_crop_output:
        return recording_config
    if ui_config and ui_config.needs_crop_output:
        return ui_config
    return recording_config or ui_config


def should_post_process_crop(
    output_path: Path,
    backend_needs_crop: bool,
    config: RecordingConfig | None,
) -> bool:
    """Return True when a stopped recording should be cropped after capture."""
    return bool(
        backend_needs_crop or
        (is_raw_recording_backup_path(output_path) and config and config.needs_crop_output)
    )


class RecorderTab(QWidget):
    """Main recorder tab with preview, settings, and controls.

    Provides a complete screen or audio-only recording interface with:
    - Live preview of the screen being captured, or an audio-only status view
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
        self._active_crop_input_path: Path | None = None
        self._active_crop_output_path: Path | None = None
        self._active_crop_filter = ""
        self._active_crop_config: RecordingConfig | None = None

        self._setup_ui()
        self._connect_signals()
        QTimer.singleShot(0, self._start_preview)

    @property
    def is_recording(self) -> bool:
        """Return whether a capture is active and must be stopped before exit."""
        return self._controller.is_recording

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

        # Preview (left side, larger). Audio-only mode swaps this for an
        # explanation instead of keeping screen capture running in the background.
        self._preview_stack = QStackedWidget()
        self._preview = RecordingPreview()
        self._preview_stack.addWidget(self._preview)
        self._audio_only_placeholder = QLabel(
            "Audio-only recording\n\n"
            "The selected input will be saved as a high-quality 48 kHz stereo AAC file.\n"
            "Screen capture is disabled."
        )
        self._audio_only_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._audio_only_placeholder.setWordWrap(True)
        self._audio_only_placeholder.setStyleSheet(
            "background: #1f1f1f; color: #bbb; font-size: 16px; padding: 32px;"
        )
        self._preview_stack.addWidget(self._audio_only_placeholder)
        self._teleprompter = TeleprompterView()
        self._preview_stack.addWidget(self._teleprompter)
        splitter.addWidget(self._preview_stack)

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

        self._permissions_btn = QPushButton("Grant Access")
        self._permissions_btn.setToolTip(
            "Request macOS screen capture and microphone permissions."
        )
        self._permissions_btn.setVisible(is_macos())
        self._permissions_btn.setStyleSheet("""
            QPushButton {
                background: #444;
                color: white;
                padding: 4px 10px;
                border: 1px solid #555;
                border-radius: 3px;
            }
            QPushButton:hover {
                background: #555;
            }
            QPushButton:disabled {
                background: #333;
                color: #777;
            }
        """)
        status_layout.addWidget(self._permissions_btn)

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
        self._permissions_btn.clicked.connect(self._on_permissions_clicked)

        # Settings panel
        self._settings_panel.screen_changed.connect(self._on_screen_changed)
        self._settings_panel.crop_mode_changed.connect(self._on_crop_mode_changed)
        self._settings_panel.audio_device_changed.connect(self._on_audio_device_changed)
        self._settings_panel.audio_volume_changed.connect(self._on_audio_volume_changed)
        self._settings_panel.audio_enabled_changed.connect(self._on_audio_enabled_changed)
        self._settings_panel.system_audio_enabled_changed.connect(self._on_system_audio_enabled_changed)
        self._settings_panel.audio_only_changed.connect(self._on_audio_only_changed)
        self._settings_panel.teleprompter_enabled_changed.connect(self._on_teleprompter_enabled_changed)
        self._settings_panel.teleprompter_script_changed.connect(self._teleprompter.set_script)
        self._settings_panel.teleprompter_text_size_changed.connect(self._teleprompter.set_text_size)
        self._settings_panel.teleprompter_speed_changed.connect(self._teleprompter.set_reading_speed)

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
        self._controller.screen_permission_status_changed.connect(self._on_screen_permission_changed)

        # Timer for updating display
        self._timer_update.timeout.connect(self._update_timer_display)
        self._crop_result_ready.connect(self._on_crop_finished)

    def _on_record_clicked(self):
        """Handle record button click."""
        # Apply current settings
        config = self._settings_panel.get_config()
        if config.needs_crop_output:
            offset_x, offset_y = self._preview.get_crop_offset()
            config.crop_offset_x = offset_x
            config.crop_offset_y = offset_y
            self._settings_panel.set_crop_offset(offset_x, offset_y)
        self._controller.set_config(config)

        self._controller.start_recording()

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
            self._teleprompter.pause()
        else:
            self._controller.resume_recording()
            if self._is_audio_teleprompter_active():
                self._teleprompter.start()

    def _on_refresh_clicked(self):
        """Handle refresh devices button click."""
        self._settings_panel.refresh_devices()

    def _start_preview(self):
        """Start preview and keep the status bar in sync."""
        if self._controller.start_preview():
            self._status_label.setText("Ready to record")

    def _on_permissions_clicked(self):
        """Request macOS recording permissions."""
        config = self._settings_panel.get_config()
        self._controller.set_config(config)

        self._permissions_btn.setEnabled(False)
        screen_granted, microphone_granted = self._controller.request_recording_permissions()
        preview_started = self._controller.start_preview()
        self._permissions_btn.setEnabled(True)

        if config.audio_only:
            if microphone_granted:
                self._status_label.setText("Ready to record audio")
            else:
                self._status_label.setText("Microphone access is required for audio recording")
        elif preview_started and microphone_granted:
            self._status_label.setText("Ready to record")
        elif preview_started:
            self._status_label.setText("Screen capture ready - check microphone access if needed")
        elif not screen_granted:
            self._status_label.setText("Grant macOS screen capture access, then reopen the app")
        else:
            self._status_label.setText("Screen preview did not start - toggle access and reopen the app")

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

    def _on_system_audio_enabled_changed(self, enabled: bool):
        """Handle macOS system audio enable/disable."""
        self._controller.set_system_audio_enabled(enabled)

    def _on_audio_only_changed(self, enabled: bool):
        """Switch the recorder between screen capture and audio-only capture."""
        self._controller.set_audio_only(enabled)
        self._update_primary_view()
        self._status_label.setText(
            "Ready to record audio" if enabled else "Ready to record"
        )

    def _on_teleprompter_enabled_changed(self, enabled: bool):
        """Show the script in place of the audio-only recording notice."""
        self._update_primary_view()

    def _is_audio_teleprompter_active(self) -> bool:
        """Return True when the current recording should drive the teleprompter."""
        return bool(
            self._settings_panel.get_config().audio_only and
            self._settings_panel.teleprompter_enabled
        )

    def _update_primary_view(self) -> None:
        """Select the preview, audio notice, or teleprompter for the recorder canvas."""
        if not self._settings_panel.get_config().audio_only:
            self._preview_stack.setCurrentWidget(self._preview)
        elif self._settings_panel.teleprompter_enabled:
            self._preview_stack.setCurrentWidget(self._teleprompter)
        else:
            self._preview_stack.setCurrentWidget(self._audio_only_placeholder)

    def _on_crop_offset_changed(self, x: float, y: float):
        """Handle crop region being moved."""
        self._settings_panel.set_crop_offset(x, y)
        self._controller.set_crop_offset(x, y)

    def _on_permission_changed(self, granted: bool):
        """Handle microphone permission result."""
        if is_macos() and not has_screen_capture_access():
            self._status_label.setText("Grant macOS screen capture access, then reopen the app")
        elif granted:
            self._status_label.setText("Microphone access granted")
        else:
            self._status_label.setText("Microphone access denied - check System Settings")
        self._permissions_btn.setEnabled(True)

    def _on_screen_permission_changed(self, granted: bool):
        """Handle screen capture permission result."""
        if granted:
            if not self._controller.is_recording:
                self._status_label.setText("Ready to record")
        else:
            self._status_label.setText("Grant macOS screen capture access, then reopen the app")
        self._permissions_btn.setEnabled(True)

    def _on_recording_started(self):
        """Handle recording started."""
        self._recording_start_time = datetime.now()
        self._record_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._pause_btn.setEnabled(self._controller.can_pause_recording())
        self._permissions_btn.setEnabled(False)
        self._settings_panel.setEnabled(False)
        config = self._controller.get_last_recording_config()
        self._status_label.setText("Recording audio..." if config and config.audio_only else "Recording...")
        if config and config.audio_only and self._settings_panel.teleprompter_enabled:
            self._teleprompter.reset()
            if self._settings_panel.teleprompter_auto_start:
                self._teleprompter.start()
        self._timer_update.start(100)

    def _on_recording_stopped(self, output_path: Path, needs_crop: bool):
        """Handle recording stopped."""
        self._timer_update.stop()
        self._teleprompter.reset()
        recording_config = self._controller.get_last_recording_config()
        ui_config = self._settings_panel.get_config()
        config = select_recording_crop_config(recording_config, ui_config)
        needs_post_crop = should_post_process_crop(output_path, needs_crop, config)
        auto_open = bool(config and not config.audio_only and not config.capture_full_screen)

        if config and (needs_crop or needs_post_crop or config.needs_crop_output):
            self._write_crop_audit(
                output_path,
                stage="recording_stopped",
                config=config,
                backend_needs_crop=needs_crop,
                effective_needs_crop=needs_post_crop,
            )
            print(
                "[Recording] Stop crop decision: "
                f"backend_needs_crop={needs_crop}, "
                f"effective_needs_crop={needs_post_crop}, "
                f"raw_path={output_path}, "
                f"config={config.to_dict()}"
            )

        if needs_post_crop:
            self._set_ui_processing()
            self._process_crop(output_path, config, auto_open=auto_open)
        else:
            self._set_ui_idle()
            self._show_completion_dialog(output_path, auto_open=auto_open)

    def _on_recording_error(self, error: str):
        """Handle recording error."""
        self._timer_update.stop()
        self._teleprompter.reset()
        self._set_ui_idle()

        QMessageBox.critical(self, "Recording Error", error)

    def _on_recording_warning(self, message: str):
        """Handle recording warning (non-fatal)."""
        if self._controller.is_recording:
            print(f"[Recording] Warning while recording: {message}")
            self._status_label.setText("Recording warning - continuing")
            return
        QMessageBox.warning(self, "Recording Warning", message)

    def _on_duration_changed(self, duration_ms: int):
        """Handle duration update."""
        self._update_timer_display()

    def _on_state_changed(self, state: RecordingState):
        """Handle state change."""
        if state == RecordingState.PAUSED:
            self._status_label.setText("Paused")
        elif state == RecordingState.RECORDING:
            self._status_label.setText(
                "Recording audio..." if self._controller.config.audio_only else "Recording..."
            )
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

        # Use the exact preview-selected crop. The raw full-screen recording is
        # kept unchanged in raw/, and this worker creates the cropped output.
        crop_filter = config.to_ffmpeg_crop_filter(screen_width, screen_height, margin=0)
        if not crop_filter:
            self._set_ui_idle()
            self._crop_auto_open = False
            self._show_crop_failure(
                input_path,
                "The recording was marked for cropping, but no crop settings were "
                "available. Raw full-screen backup saved at:\n"
                f"{input_path}"
            )
            return

        output_path = cropped_recording_output_path(input_path)
        print(
            "[Recording] Creating cropped output: "
            f"{output_path} from {input_path} using {crop_filter}"
        )
        self._write_crop_audit(
            input_path,
            stage="crop_queued",
            config=config,
            effective_needs_crop=True,
            crop_filter=crop_filter,
            output_path=output_path,
            screen_size=(screen_width, screen_height),
        )

        self._crop_auto_open = auto_open
        self._active_crop_input_path = input_path
        self._active_crop_output_path = output_path
        self._active_crop_filter = crop_filter
        self._active_crop_config = config.copy()
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

        if not success:
            self._crop_auto_open = False
            if self._active_crop_input_path:
                self._write_crop_audit(
                    self._active_crop_input_path,
                    stage="crop_failed",
                    config=self._active_crop_config,
                    effective_needs_crop=True,
                    crop_filter=self._active_crop_filter,
                    output_path=self._active_crop_output_path,
                    success=False,
                    message=message,
                )
            self._clear_active_crop()
            self._show_crop_failure(result_path, message)
            return

        if self._active_crop_input_path:
            self._write_crop_audit(
                self._active_crop_input_path,
                stage="crop_finished",
                config=self._active_crop_config,
                effective_needs_crop=True,
                crop_filter=self._active_crop_filter,
                output_path=result_path,
                success=True,
                message=message,
            )
        self._clear_active_crop()
        self._show_completion_dialog(result_path, auto_open=self._crop_auto_open)

    def _clear_active_crop(self) -> None:
        self._active_crop_input_path = None
        self._active_crop_output_path = None
        self._active_crop_filter = ""
        self._active_crop_config = None

    def _show_crop_failure(self, raw_path: Path, message: str) -> None:
        """Warn that crop output was not created without opening the raw backup."""
        self._status_label.setText("Cropping failed - raw backup saved")
        warning = message or (
            "Cropping failed. Raw full-screen backup saved at:\n"
            f"{raw_path}"
        )
        QMessageBox.warning(self, "Crop Failed", warning)
        self.recording_completed.emit(raw_path)

    def _write_crop_audit(
        self,
        raw_path: Path,
        *,
        stage: str,
        config: RecordingConfig | None,
        backend_needs_crop: bool | None = None,
        effective_needs_crop: bool | None = None,
        crop_filter: str | None = None,
        output_path: Path | None = None,
        screen_size: tuple[int, int] | None = None,
        success: bool | None = None,
        message: str = "",
    ) -> None:
        """Write a small crop sidecar so recorder decisions can be inspected."""
        audit_path = cropped_recording_output_path(raw_path).with_suffix(".crop.json")
        cropped_path = output_path or cropped_recording_output_path(raw_path)
        payload = {
            "raw_path": str(raw_path),
            "cropped_output_path": str(cropped_path),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

        try:
            if audit_path.exists():
                payload.update(json.loads(audit_path.read_text(encoding="utf-8")))
        except Exception:
            pass

        payload.update({
            "raw_path": str(raw_path),
            "cropped_output_path": str(cropped_path),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        event = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "stage": stage,
            "backend_needs_crop": backend_needs_crop,
            "effective_needs_crop": effective_needs_crop,
            "crop_filter": crop_filter,
            "screen_size": list(screen_size) if screen_size else None,
            "success": success,
            "message": message,
            "config": config.to_dict() if config else None,
        }
        events = payload.setdefault("events", [])
        if isinstance(events, list):
            events.append(event)
        else:
            payload["events"] = [event]

        try:
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            audit_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"[Recording] Failed to write crop audit file: {exc}")

    def _show_completion_dialog(self, output_path: Path, auto_open: bool = False):
        """Show recording completion dialog or auto-open."""
        self.recording_completed.emit(output_path)

        if auto_open:
            self.open_in_editor_requested.emit(output_path)
            return

        is_audio_only = output_path.suffix.lower() in {".m4a", ".aac", ".wav", ".mp3"}
        title = "Audio Recording Complete" if is_audio_only else "Recording Complete"
        noun = "Audio recording" if is_audio_only else "Recording"
        reply = QMessageBox.question(
            self,
            title,
            f"{noun} saved to:\n{output_path}\n\nOpen in editor?",
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
        self._permissions_btn.setEnabled(True)
        self._settings_panel.setEnabled(True)
        self._status_label.setText("Ready to record")

    def get_config(self) -> RecordingConfig:
        """Get current recording configuration."""
        return self._settings_panel.get_config()

    def set_config(self, config: RecordingConfig):
        """Set recording configuration."""
        self._settings_panel.set_config(config)
        self._controller.set_config(config)
        self._controller.set_audio_only(config.audio_only)
        self._update_primary_view()
        if config.capture_full_screen:
            self._preview.set_crop_mode(None, None)
        else:
            self._preview.set_crop_mode(config.target_resolution, config.target_aspect_ratio)
            self._preview.set_crop_offset(config.crop_offset_x, config.crop_offset_y)

    def showEvent(self, event):
        """Handle widget becoming visible."""
        super().showEvent(event)
        # Restart preview when tab becomes visible
        if not self._controller.is_recording:
            self._start_preview()

    def hideEvent(self, event):
        """Handle widget being hidden."""
        super().hideEvent(event)
        # Stop preview when tab is hidden (to save resources)
        if not self._controller.is_recording:
            self._controller.stop_preview()
            self._teleprompter.pause()
