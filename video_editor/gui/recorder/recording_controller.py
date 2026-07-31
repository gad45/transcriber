"""Controller for screen and audio recording using Qt6 multimedia."""

from datetime import datetime
from pathlib import Path
from enum import Enum, auto

import difflib
import re
import struct

from PySide6.QtCore import (
    QObject, Signal, QUrl, QStandardPaths, QTimer, QIODevice, QBuffer,
    QMicrophonePermission, Qt, QCoreApplication
)
from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtMultimedia import (
    QMediaCaptureSession,
    QScreenCapture,
    QAudioInput,
    QMediaRecorder,
    QMediaFormat,
    QMediaDevices,
    QAudioDevice,
    QAudioSource,
    QAudioFormat,
)

from ..models import RecordingConfig
from .ffmpeg_recorder import FFmpegRecorder
from .macos_native_recorder import NativeMacOSRecorder
from .macos_permissions import has_screen_capture_access, is_macos, request_screen_capture_access


class RecordingState(Enum):
    """Recording state machine states."""
    IDLE = auto()
    RECORDING = auto()
    PAUSED = auto()
    PROCESSING = auto()  # Post-processing (FFmpeg crop)


class RecordingController(QObject):
    """Controls screen and audio recording using Qt6 multimedia.

    This controller wraps Qt multimedia classes to provide a simple API
    for recording the screen with audio. It handles:
    - Screen capture via QScreenCapture
    - Audio input via QAudioInput
    - Recording to file via QMediaRecorder
    - State management and error handling

    Signals:
        recording_started: Emitted when recording begins
        recording_stopped: Emitted when recording ends (path, needs_crop)
        recording_error: Emitted on error (error_string)
        duration_changed: Emitted periodically with current duration (ms)
        state_changed: Emitted when recording state changes
        audio_level_changed: Emitted with current audio level (0.0-1.0)
    """

    recording_started = Signal()
    recording_stopped = Signal(Path, bool)  # output_path, needs_ffmpeg_crop
    recording_error = Signal(str)
    recording_warning = Signal(str)
    duration_changed = Signal(int)  # milliseconds
    state_changed = Signal(RecordingState)
    audio_level_changed = Signal(float)  # 0.0-1.0
    permission_status_changed = Signal(bool)  # True if microphone permission granted
    screen_permission_status_changed = Signal(bool)  # True if screen capture permission granted

    def __init__(self, parent=None):
        super().__init__(parent)

        self._config = RecordingConfig()
        self._recording_config: RecordingConfig | None = None
        self._state = RecordingState.IDLE
        self._output_path: Path | None = None
        self._preview_active = False
        self._resume_preview_after_external_recording = False
        self._audio_only_recorder_active = False
        self._resume_preview_after_audio_only_recording = False
        self._permission_checked = False  # Skip repeated permission checks
        self._use_ffmpeg_recording = False  # Legacy direct-crop mode; default preserves full-screen backups
        self._stop_requested = False

        # FFmpeg recorder for direct crop recording
        self._ffmpeg_recorder = FFmpegRecorder(self)
        self._ffmpeg_recorder.recording_started.connect(self._on_ffmpeg_started)
        self._ffmpeg_recorder.recording_stopped.connect(self._on_ffmpeg_stopped)
        self._ffmpeg_recorder.recording_error.connect(self._on_ffmpeg_error)
        self._ffmpeg_recorder.recording_warning.connect(self._on_ffmpeg_warning)
        self._ffmpeg_recorder.duration_changed.connect(self._on_ffmpeg_duration)

        # Native macOS recorder for system audio capture
        self._native_recorder = NativeMacOSRecorder(self)
        self._native_recorder.recording_started.connect(self._on_native_started)
        self._native_recorder.recording_stopped.connect(self._on_native_stopped)
        self._native_recorder.recording_error.connect(self._on_native_error)
        self._native_recorder.recording_warning.connect(self._on_native_warning)
        self._native_recorder.duration_changed.connect(self._on_native_duration)

        # Qt multimedia objects
        self._session = QMediaCaptureSession()
        self._screen_capture = QScreenCapture()
        self._audio_input: QAudioInput | None = None
        self._recorder = QMediaRecorder()

        # Audio monitoring (separate from recording)
        self._audio_source: QAudioSource | None = None
        self._audio_io_device: QIODevice | None = None
        self._level_timer = QTimer(self)
        self._level_timer.timeout.connect(self._update_audio_level)

        self._setup_capture_session()
        self._connect_signals()

        stale_outputs = FFmpegRecorder.stop_orphaned_recordings()
        if stale_outputs:
            paths = "\n".join(str(path) for path in stale_outputs)
            QTimer.singleShot(
                0,
                lambda: self.recording_warning.emit(
                    "Stopped an unfinished recorder process from a previous app session "
                    "to prevent continued microphone capture and disk use.\n\n"
                    f"Finalizing file(s):\n{paths}"
                ),
            )

    def _setup_capture_session(self):
        """Initialize the capture session with components."""
        self._session.setScreenCapture(self._screen_capture)
        self._session.setRecorder(self._recorder)

        self._configure_video_recorder_format()

    def _configure_video_recorder_format(self) -> None:
        """Configure the shared recorder for screen recordings."""
        format = QMediaFormat()
        format.setFileFormat(QMediaFormat.FileFormat.MPEG4)
        format.setVideoCodec(QMediaFormat.VideoCodec.H264)
        format.setAudioCodec(QMediaFormat.AudioCodec.AAC)
        self._recorder.setMediaFormat(format)

        # Set high quality defaults
        self._recorder.setQuality(QMediaRecorder.Quality.VeryHighQuality)

        # Configure high-quality audio settings
        self._recorder.setAudioSampleRate(48000)  # Professional 48kHz
        self._recorder.setAudioChannelCount(2)     # Stereo
        self._recorder.setAudioBitRate(256000)     # 256 kbps for high quality AAC

        print(f"[Audio] Recorder configured: {self._recorder.audioSampleRate()}Hz, "
              f"{self._recorder.audioChannelCount()}ch, {self._recorder.audioBitRate()}bps")

    def _configure_audio_only_recorder_format(self) -> None:
        """Configure a high-quality audio-only M4A recording."""
        format = QMediaFormat()
        m4a_format = getattr(QMediaFormat.FileFormat, "Mpeg4Audio", None)
        format.setFileFormat(m4a_format or QMediaFormat.FileFormat.MPEG4)
        format.setAudioCodec(QMediaFormat.AudioCodec.AAC)
        self._recorder.setMediaFormat(format)
        self._recorder.setQuality(QMediaRecorder.Quality.VeryHighQuality)
        self._recorder.setAudioSampleRate(48000)
        self._recorder.setAudioChannelCount(2)
        self._recorder.setAudioBitRate(256000)

    def _connect_signals(self):
        """Connect Qt signals to handlers."""
        self._recorder.durationChanged.connect(self._on_duration_changed)
        self._recorder.errorOccurred.connect(self._on_recorder_error)
        self._recorder.recorderStateChanged.connect(self._on_recorder_state_changed)
        self._recorder.actualLocationChanged.connect(self._on_location_changed)
        self._screen_capture.errorOccurred.connect(self._on_capture_error)

    def _on_duration_changed(self, duration_ms: int):
        """Handle duration updates during recording."""
        self.duration_changed.emit(duration_ms)

    def _on_recorder_error(self, error, error_string: str):
        """Handle recorder errors."""
        message = self._describe_backend_error("Qt recorder error", error, error_string)
        self._set_state(RecordingState.IDLE)
        self._stop_requested = False
        self._restore_after_audio_only_recording()
        self._resume_audio_monitoring_if_needed()
        self.recording_error.emit(message)

    def _on_capture_error(self, error, error_string: str):
        """Handle screen capture errors."""
        if self._audio_only_recorder_active:
            # The audio-only recorder deliberately detaches the screen source.
            # Some backends report that normal shutdown as a capture error.
            self._screen_capture.setActive(False)
            return

        was_preview_active = self._preview_active
        was_recording = self._state in (RecordingState.RECORDING, RecordingState.PAUSED)
        stop_requested = self._stop_requested
        message = self._describe_backend_error("Screen capture error", error, error_string)

        if was_recording and stop_requested:
            # QScreenCapture can report a shutdown error while QMediaRecorder is
            # still finalizing after a user stop. Keep the recorder state intact
            # so the StoppedState signal can finish the file normally.
            self._screen_capture.setActive(False)
            if was_preview_active:
                self._stop_audio_monitoring()
            return

        self._set_state(RecordingState.IDLE)
        self._stop_requested = False
        self._screen_capture.setActive(False)
        self._preview_active = False
        if was_preview_active:
            self._stop_audio_monitoring()
        if was_recording:
            message = (
                "Recording stopped because screen capture failed before Stop was pressed.\n\n"
                f"{message}"
                f"{self._recording_file_details()}"
            )
        self.recording_error.emit(message)

    def _on_recorder_state_changed(self, state):
        """Handle recorder state changes."""
        if state == QMediaRecorder.RecorderState.StoppedState:
            if self._state in (RecordingState.RECORDING, RecordingState.PAUSED):
                if not self._stop_requested:
                    self._handle_unexpected_qt_stop()
                    return
                self._finalize_recording()

    def _on_location_changed(self, location: QUrl):
        """Handle actual output location being set."""
        if location.isLocalFile():
            self._output_path = Path(location.toLocalFile())

    def _set_state(self, new_state: RecordingState):
        """Update internal state and emit signal."""
        if self._state != new_state:
            self._state = new_state
            self.state_changed.emit(new_state)

    def _finalize_recording(self):
        """Finalize recording after stop."""
        if self._output_path and self._output_path.exists():
            config = self._recording_config or self._config
            needs_crop = config.needs_crop_output
            self._set_state(RecordingState.IDLE)
            self._stop_requested = False
            self._restore_after_audio_only_recording()
            self._resume_audio_monitoring_if_needed()
            self.recording_stopped.emit(self._output_path, needs_crop)
        else:
            self._set_state(RecordingState.IDLE)
            self._stop_requested = False
            self._restore_after_audio_only_recording()
            self._resume_audio_monitoring_if_needed()
            self.recording_error.emit(
                "Recording stopped, but the output file was not created."
                f"{self._recording_file_details()}"
            )

    def _restore_after_audio_only_recording(self) -> None:
        """Reconnect the screen source after an audio-only capture finishes."""
        if not self._audio_only_recorder_active:
            return

        self._audio_only_recorder_active = False
        self._session.setScreenCapture(self._screen_capture)
        resume_preview = self._resume_preview_after_audio_only_recording
        self._resume_preview_after_audio_only_recording = False
        self._configure_video_recorder_format()

        if resume_preview and not self._config.audio_only:
            self.start_preview()

    def _resume_audio_monitoring_if_needed(self) -> None:
        """Resume levels after recording when a preview or audio-only mode needs them."""
        if self._preview_active or self._config.audio_only:
            self._start_audio_monitoring()

    @staticmethod
    def _describe_backend_error(prefix: str, error, error_string: str) -> str:
        """Return a useful backend error even when Qt provides an empty string."""
        detail = (error_string or "").strip()
        error_name = getattr(error, "name", str(error)).strip()
        if detail and error_name:
            return f"{prefix}: {detail} ({error_name})"
        if detail:
            return f"{prefix}: {detail}"
        if error_name:
            return f"{prefix}: {error_name}"
        return prefix

    def _recording_file_details(self) -> str:
        """Describe the current output file for recovery/debug messages."""
        if self._output_path is None:
            return "\n\nNo output path had been assigned yet."

        if self._output_path.exists():
            try:
                size_mb = self._output_path.stat().st_size / (1024 * 1024)
                return (
                    "\n\nPartial recording saved at:\n"
                    f"{self._output_path}\n"
                    f"Size: {size_mb:.1f} MB"
                )
            except OSError:
                return f"\n\nPartial recording saved at:\n{self._output_path}"

        return (
            "\n\nExpected output path:\n"
            f"{self._output_path}\n"
            "The file was not created."
        )

    def _handle_unexpected_qt_stop(self) -> None:
        """Report a Qt backend stop that was not initiated by the user."""
        self._set_state(RecordingState.IDLE)
        self._stop_requested = False
        self._screen_capture.setActive(False)
        self._preview_active = False
        self._stop_audio_monitoring()
        self._restore_after_audio_only_recording()

        recorder_error = self._recorder.error()
        recorder_error_string = self._recorder.errorString()
        details = self._describe_backend_error(
            "Qt recorder stopped unexpectedly",
            recorder_error,
            recorder_error_string,
        )
        self.recording_error.emit(
            "Recording stopped before Stop was pressed.\n\n"
            f"{details}"
            f"{self._recording_file_details()}"
        )

    @property
    def state(self) -> RecordingState:
        """Get current recording state."""
        return self._state

    @property
    def config(self) -> RecordingConfig:
        """Get current recording configuration."""
        return self._config

    @property
    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self._state == RecordingState.RECORDING

    def set_config(self, config: RecordingConfig):
        """Update recording configuration."""
        self._config = config.copy()
        if self._config.audio_only:
            self._config.audio_enabled = True
            self._config.system_audio_enabled = False
        self._apply_config()

    def _apply_config(self):
        """Apply current configuration to capture components."""
        # Set screen
        screens = self.get_available_screens()
        if 0 <= self._config.screen_index < len(screens):
            self._screen_capture.setScreen(screens[self._config.screen_index])

        # Set audio device
        if self._config.audio_enabled:
            self._setup_audio_input()
        else:
            self._session.setAudioInput(None)
            self._audio_input = None

        # Set quality
        quality_map = {
            "low": QMediaRecorder.Quality.LowQuality,
            "medium": QMediaRecorder.Quality.NormalQuality,
            "high": QMediaRecorder.Quality.HighQuality,
            "very_high": QMediaRecorder.Quality.VeryHighQuality,
        }
        self._recorder.setQuality(quality_map.get(self._config.video_quality, QMediaRecorder.Quality.HighQuality))

    def _setup_audio_input(self):
        """Set up audio input with current config."""
        # Clean up existing audio input first
        if self._audio_input is not None:
            print("[Audio] Cleaning up existing audio input")
            self._session.setAudioInput(None)
            self._audio_input = None

        devices = self.get_available_audio_devices()
        print(f"[Audio] Available input devices: {len(devices)}")
        for d in devices:
            print(f"[Audio]   - {d.description()} (id: {d.id().data().decode()[:20]}...)")

        # Find device by ID or use default
        device = None
        if self._config.audio_device_id:
            for d in devices:
                if d.id().data().decode() == self._config.audio_device_id:
                    device = d
                    break

        if device is None and devices:
            device = QMediaDevices.defaultAudioInput()
            print(f"[Audio] Using default device")

        if device:
            print(f"[Audio] Setting up recording input: {device.description()}")
            self._audio_input = QAudioInput(device)
            self._audio_input.setVolume(self._config.audio_volume)
            self._session.setAudioInput(self._audio_input)
            print(f"[Audio] Audio input connected to session, volume: {self._config.audio_volume}")
        else:
            print("[Audio] ERROR: No audio device found for recording")

    def _get_output_path(self) -> Path:
        """Generate output file path based on config.

        Screen recordings are saved to a 'raw' subdirectory to ensure they are
        never lost during post-processing (cropping). Audio-only recordings are
        already final files, so they are saved directly to the output folder.
        """
        # Determine base output directory
        if self._config.output_directory:
            base_dir = Path(self._config.output_directory)
        else:
            movies_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
            base_dir = Path(movies_path) / "Recordings"

        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self._config.filename_pattern.format(timestamp=timestamp)

        if self._config.audio_only:
            output_path = base_dir / f"{filename}.m4a"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"[Recording] Audio file will be saved to: {output_path}")
            return output_path

        # Raw recordings go in a subdirectory - never deleted automatically
        raw_dir = base_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{filename}.{self._config.container_format}"

        print(f"[Recording] Raw file will be saved to: {raw_dir / filename}")
        return raw_dir / filename

    def set_use_ffmpeg_recording(self, use_ffmpeg: bool):
        """Toggle between FFmpeg and Qt recording.

        Direct FFmpeg crop recording does not keep a full-screen backup. The
        default GUI path records full screen first and crops afterward.
        """
        self._use_ffmpeg_recording = use_ffmpeg

    def start_recording(self) -> bool:
        """Start recording with current configuration.

        Returns:
            True if recording started successfully, False otherwise
        """
        if self._state != RecordingState.IDLE:
            return False

        self._stop_requested = False

        if self._config.audio_only:
            if not self._config.audio_enabled:
                self.recording_error.emit("Enable an input device before starting an audio-only recording.")
                return False
            if not self.check_microphone_permission():
                self.recording_warning.emit(
                    "Microphone access was requested. Approve the macOS prompt, then start recording again."
                )
                return False

            self._recording_config = self._config.copy()
            return self._start_audio_only_recording()

        if not self.check_screen_capture_permission(request_if_needed=True):
            if not is_macos():
                self.recording_warning.emit(self._screen_permission_message())
                return False
            self.recording_warning.emit(
                "macOS has not confirmed screen recording access yet. "
                "Attempting to start recording anyway; if it fails, toggle "
                "Video Editor off and on in Screen & System Audio Recording."
            )

        if self._should_use_native_macos_recording():
            self._recording_config = self._config.copy()
            return self._start_macos_native_recording()

        if self._config.audio_enabled and not self.check_microphone_permission():
            self.recording_warning.emit(
                "Microphone access was requested. Approve the macOS prompt, then start recording again."
            )
            return False

        # Snapshot config used for this recording (for post-crop consistency)
        self._recording_config = self._config.copy()

        # Cropped recordings must keep the full-screen raw file as a backup.
        # Record full screen first, then let the recorder tab create the
        # cropped parent-folder output from the raw file.
        if self._use_ffmpeg_recording and not self._config.capture_full_screen:
            if self._config.audio_enabled or self._config.needs_crop_output:
                return self._start_qt_recording()
            return self._start_ffmpeg_recording()

        # Use Qt for full-screen recordings
        return self._start_qt_recording()

    def _start_qt_recording(self) -> bool:
        """Start recording using Qt multimedia (full screen)."""
        try:
            # Stop audio monitoring to release the device for recording
            self._stop_audio_monitoring()

            self._session.setScreenCapture(self._screen_capture)
            self._configure_video_recorder_format()
            self._apply_config()

            # Set output location
            output_path = self._get_output_path()
            self._recorder.setOutputLocation(QUrl.fromLocalFile(str(output_path)))
            self._output_path = output_path

            # Start capture and recording
            self._screen_capture.setActive(True)
            self._recorder.record()

            self._set_state(RecordingState.RECORDING)
            self.recording_started.emit()
            return True

        except Exception as e:
            self.recording_error.emit(str(e))
            return False

    def _start_audio_only_recording(self) -> bool:
        """Start audio-only recording with the native Qt multimedia backend."""
        try:
            self._stop_audio_monitoring()

            # QMediaRecorder uses macOS's AVFoundation capture pipeline here.
            # This is intentionally separate from the direct FFmpeg input used
            # for optional screen-crop recordings: the native pipeline has
            # proven reliable for uninterrupted microphone capture.
            self._resume_preview_after_audio_only_recording = self._preview_active
            self._audio_only_recorder_active = True
            self._screen_capture.setActive(False)
            self._preview_active = False
            self._session.setScreenCapture(None)

            self._apply_config()
            self._configure_audio_only_recorder_format()
            output_path = self._get_output_path()
            self._recorder.setOutputLocation(QUrl.fromLocalFile(str(output_path)))
            self._output_path = output_path
            self._recorder.record()

            self._set_state(RecordingState.RECORDING)
            self.recording_started.emit()
            return True
        except Exception as e:
            self._restore_after_audio_only_recording()
            self._resume_audio_monitoring_if_needed()
            self.recording_error.emit(str(e))
            return False

    def _should_use_native_macos_recording(self) -> bool:
        """Return True when the native macOS recorder should be used."""
        return bool(is_macos() and self._config.system_audio_enabled)

    def _start_macos_native_recording(self) -> bool:
        """Start recording with the native macOS system-audio backend."""
        try:
            self._stop_audio_monitoring()
            self._suspend_preview_for_external_recording()

            output_path = self._get_output_path()
            self._output_path = output_path

            microphone_name = None
            qt_device = self._get_qt_audio_device()
            if self._config.audio_enabled and qt_device is not None:
                microphone_name = qt_device.description()

            started = self._native_recorder.start_recording(
                screen_index=self._config.screen_index,
                output_path=output_path,
                capture_system_audio=True,
                capture_microphone=self._config.audio_enabled,
                microphone_name=microphone_name,
                frame_rate=30,
                sample_rate=self._config.audio_sample_rate,
                channel_count=2,
            )
            if not started:
                self._resume_preview_after_external_recording_if_needed()
            return started
        except Exception as e:
            self._resume_preview_after_external_recording_if_needed()
            self.recording_error.emit(str(e))
            return False

    def _start_ffmpeg_recording(self) -> bool:
        """Start recording using FFmpeg with crop applied during capture."""
        try:
            # Stop audio monitoring to release the device for recording
            self._stop_audio_monitoring()
            self._suspend_preview_for_external_recording()

            # Get screen dimensions
            screens = QGuiApplication.screens()
            if self._config.screen_index >= len(screens):
                self._resume_preview_after_external_recording_if_needed()
                self.recording_error.emit("Invalid screen index")
                return False

            screen = screens[self._config.screen_index]
            screen_w, screen_h = self.get_screen_pixel_size(screen)

            # Calculate crop region (no margin needed for FFmpeg)
            crop_rect = self._config.get_crop_rect(screen_w, screen_h, margin=0)

            # Get FFmpeg audio device index
            audio_device_index = self._get_ffmpeg_audio_device_index()

            # Prefer the selected device's native format for better quality
            # Keep FFmpeg audio at the configured quality; ffmpeg/CoreAudio
            # will resample if the device doesn't natively support it.
            audio_sample_rate = self._config.audio_sample_rate
            audio_channels = 2

            # Get output path
            output_path = self._get_output_path()
            self._output_path = output_path

            # Record to MKV for resilience, then remux to MP4 for final output
            final_output_path: Path | None = None
            raw_output_path = output_path
            if output_path.suffix.lower() == ".mp4":
                raw_output_path = output_path.with_suffix(".mkv")
                final_output_path = output_path.parent.parent / output_path.name
                self._output_path = final_output_path

            # Start FFmpeg recording
            started = self._ffmpeg_recorder.start_recording(
                screen_index=self._config.screen_index,
                crop_rect=crop_rect,
                audio_device_index=audio_device_index,
                output_path=raw_output_path,
                final_output_path=final_output_path,
                audio_sample_rate=audio_sample_rate,
                audio_channels=audio_channels,
                use_hardware=True,
                framerate=30
            )
            if not started:
                self._resume_preview_after_external_recording_if_needed()
            return started

        except Exception as e:
            self._resume_preview_after_external_recording_if_needed()
            self.recording_error.emit(str(e))
            return False

    def _get_ffmpeg_audio_device_index(self) -> int:
        """Map Qt audio device ID to FFmpeg avfoundation index."""
        if not self._config.audio_enabled:
            return -1  # No audio

        # Get FFmpeg device list
        ffmpeg_devices = FFmpegRecorder.get_ffmpeg_audio_devices()

        if not ffmpeg_devices:
            return 0  # Default device

        # Try to match by name
        qt_device = self._get_qt_audio_device()
        if qt_device is not None:
            qt_name = qt_device.description()
            match = self._match_ffmpeg_audio_device(qt_name, ffmpeg_devices)
            if match is not None:
                match_idx, match_name, match_score = match
                if match_score < 0.6:
                    self.recording_warning.emit(
                        "Audio device match is weak. FFmpeg may use a different input "
                        "than the one selected in the UI.\n\n"
                        f"Selected: {qt_name}\n"
                        f"Matched: {match_name} (index {match_idx}, score {match_score:.2f})"
                    )
                return match_idx
            self.recording_warning.emit(
                "Selected audio device not found in FFmpeg device list. "
                "Falling back to the first FFmpeg device.\n\n"
                f"Selected: {qt_name}"
            )

        # Fallback to first FFmpeg device
        return ffmpeg_devices[0][0]

    def _on_ffmpeg_started(self):
        """Handle FFmpeg recording started."""
        self._set_state(RecordingState.RECORDING)
        self.recording_started.emit()

    def _on_native_started(self):
        """Handle native macOS recording started."""
        self._set_state(RecordingState.RECORDING)
        self.recording_started.emit()

    def _on_ffmpeg_stopped(self, output_path: Path):
        """Handle FFmpeg recording stopped."""
        self._set_state(RecordingState.IDLE)
        self._stop_requested = False
        if self._config.audio_only:
            self._setup_audio_input()
            self._resume_audio_monitoring_if_needed()
        else:
            self._resume_preview_after_external_recording_if_needed()
        # FFmpeg recordings are already cropped, no post-processing needed
        self.recording_stopped.emit(output_path, False)

    def _on_native_stopped(self, output_path: Path):
        """Handle native macOS recording stopped."""
        self._output_path = output_path
        self._set_state(RecordingState.IDLE)
        self._stop_requested = False
        self._resume_preview_after_external_recording_if_needed()
        config = self._recording_config or self._config
        self.recording_stopped.emit(output_path, config.needs_crop_output)

    def _on_ffmpeg_error(self, error: str):
        """Handle FFmpeg recording error."""
        self._set_state(RecordingState.IDLE)
        self._stop_requested = False
        if self._config.audio_only:
            self._setup_audio_input()
            self._resume_audio_monitoring_if_needed()
        else:
            self._resume_preview_after_external_recording_if_needed()
        self.recording_error.emit(error)

    def _on_native_error(self, error: str):
        """Handle native macOS recording error."""
        self._set_state(RecordingState.IDLE)
        self._stop_requested = False
        self._resume_preview_after_external_recording_if_needed()
        self.recording_error.emit(error)

    def _on_ffmpeg_warning(self, message: str):
        """Handle FFmpeg recording warning (non-fatal)."""
        self.recording_warning.emit(message)

    def _on_native_warning(self, message: str):
        """Handle native macOS warning (non-fatal)."""
        self.recording_warning.emit(message)

    def _on_ffmpeg_duration(self, duration: float):
        """Handle FFmpeg duration update."""
        self.duration_changed.emit(int(duration * 1000))

    def _on_native_duration(self, duration: float):
        """Handle native macOS duration update."""
        self.duration_changed.emit(int(duration * 1000))

    def stop_recording(self):
        """Stop the current recording."""
        if self._state not in (RecordingState.RECORDING, RecordingState.PAUSED):
            return

        self._stop_requested = True

        # Check if using FFmpeg recorder
        if self._ffmpeg_recorder.is_recording:
            self._ffmpeg_recorder.stop_recording()
        elif self._native_recorder.is_recording:
            self._native_recorder.stop_recording()
        else:
            self._recorder.stop()
            if not self._audio_only_recorder_active:
                self._screen_capture.setActive(False)

    def pause_recording(self):
        """Pause the current recording."""
        if self._state != RecordingState.RECORDING:
            return

        if not self.can_pause_recording():
            return

        self._recorder.pause()
        self._set_state(RecordingState.PAUSED)

    def resume_recording(self):
        """Resume a paused recording."""
        if self._state != RecordingState.PAUSED:
            return

        if not self.can_pause_recording():
            return

        self._recorder.record()
        self._set_state(RecordingState.RECORDING)

    def can_pause_recording(self) -> bool:
        """Return True when the active recorder backend supports pause."""
        return not self._ffmpeg_recorder.is_recording and not self._native_recorder.is_recording

    def set_screen(self, index: int):
        """Set the screen to capture."""
        self._config.screen_index = index
        screens = self.get_available_screens()
        if 0 <= index < len(screens):
            self._screen_capture.setScreen(screens[index])

    def set_audio_device(self, device_id: str):
        """Set the audio input device."""
        self._config.audio_device_id = device_id
        if self._config.audio_enabled:
            self._setup_audio_input()
            # Restart audio monitoring with new device
            self.restart_audio_monitoring()

    def set_audio_volume(self, volume: float):
        """Set the audio input volume (0.0-1.0)."""
        self._config.audio_volume = max(0.0, min(1.0, volume))
        if self._audio_input:
            self._audio_input.setVolume(self._config.audio_volume)

    def set_audio_enabled(self, enabled: bool):
        """Enable or disable audio recording."""
        if self._config.audio_only:
            enabled = True
        self._config.audio_enabled = enabled
        if enabled:
            self._setup_audio_input()
        else:
            self._session.setAudioInput(None)
            self._audio_input = None

    def set_system_audio_enabled(self, enabled: bool):
        """Enable or disable native macOS system audio recording."""
        self._config.system_audio_enabled = enabled and not self._config.audio_only

    def set_audio_only(self, enabled: bool):
        """Enable or disable capture that writes only high-quality input audio."""
        self._config.audio_only = enabled
        if enabled:
            self._config.audio_enabled = True
            self._config.system_audio_enabled = False
            self.stop_preview()
            self._setup_audio_input()
            self._start_audio_monitoring()
        else:
            self._session.setScreenCapture(self._screen_capture)
            self._configure_video_recorder_format()
            self.start_preview()

    def set_aspect_ratio(self, ratio: tuple[int, int] | None):
        """Set the target aspect ratio for cropping.

        Args:
            ratio: Tuple of (width, height) for aspect ratio, or None for full screen
        """
        if ratio is None:
            self._config.capture_full_screen = True
            self._config.target_aspect_ratio = None
        else:
            self._config.capture_full_screen = False
            self._config.target_aspect_ratio = ratio

    def set_crop_mode(self, resolution: tuple[int, int] | None, aspect_ratio: tuple[int, int] | None):
        """Set the crop mode (resolution or aspect ratio).

        Args:
            resolution: Fixed resolution (takes precedence), or None
            aspect_ratio: Aspect ratio, or None
        """
        if resolution is None and aspect_ratio is None:
            self._config.capture_full_screen = True
            self._config.target_resolution = None
            self._config.target_aspect_ratio = None
        else:
            self._config.capture_full_screen = False
            self._config.target_resolution = resolution
            self._config.target_aspect_ratio = aspect_ratio

    def set_crop_offset(self, x: float, y: float):
        """Set the crop region offset (normalized 0.0-1.0)."""
        self._config.crop_offset_x = max(0.0, min(1.0, x))
        self._config.crop_offset_y = max(0.0, min(1.0, y))

    def get_video_sink(self):
        """Get the video sink for preview display.

        Returns:
            The QMediaCaptureSession for connecting to a video output
        """
        return self._session

    def get_last_recording_config(self) -> RecordingConfig | None:
        """Get the config snapshot used for the most recent recording."""
        return self._recording_config.copy() if self._recording_config else None

    def _get_qt_audio_device(self) -> QAudioDevice | None:
        """Get the Qt audio input device for the current selection."""
        if not self._config.audio_enabled:
            return None
        if self._config.audio_device_id:
            for device in QMediaDevices.audioInputs():
                if device.id().data().decode() == self._config.audio_device_id:
                    return device
        return QMediaDevices.defaultAudioInput()

    @staticmethod
    def _select_audio_format(
        device: QAudioDevice,
        sample_rate: int,
        channels: int
    ) -> tuple[int, int]:
        """Prefer the requested format if supported; fall back to device preferred."""
        desired = QAudioFormat()
        desired.setSampleRate(sample_rate)
        desired.setChannelCount(channels)
        desired.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if device.isFormatSupported(desired):
            return sample_rate, channels

        fmt = device.preferredFormat()
        fallback_rate = fmt.sampleRate() or sample_rate
        fallback_channels = fmt.channelCount() or channels
        return fallback_rate, fallback_channels

    @staticmethod
    def _normalize_audio_name(name: str) -> str:
        """Normalize device names for fuzzy matching."""
        return re.sub(r"[^a-z0-9]+", "", name.lower())

    def _match_ffmpeg_audio_device(
        self,
        qt_name: str,
        ffmpeg_devices: list[tuple[int, str]]
    ) -> tuple[int, str, float] | None:
        """Find the best FFmpeg audio device match for a Qt device name."""
        target = self._normalize_audio_name(qt_name)
        if not target:
            return None

        best_idx = None
        best_name = ""
        best_score = 0.0
        for idx, name in ffmpeg_devices:
            candidate = self._normalize_audio_name(name)
            if not candidate:
                continue
            score = difflib.SequenceMatcher(None, target, candidate).ratio()
            if target in candidate or candidate in target:
                score += 0.1
            if score > best_score:
                best_score = score
                best_idx = idx
                best_name = name

        if best_idx is None:
            return None
        return best_idx, best_name, best_score

    @staticmethod
    def get_available_screens() -> list[QScreen]:
        """Get list of available screens for capture."""
        return QGuiApplication.screens()

    @staticmethod
    def get_screen_pixel_size(screen: QScreen) -> tuple[int, int]:
        """Get screen size in physical pixels (accounts for device pixel ratio)."""
        geo = screen.geometry()
        dpr = screen.devicePixelRatio()
        return (int(round(geo.width() * dpr)), int(round(geo.height() * dpr)))

    @staticmethod
    def get_available_audio_devices() -> list[QAudioDevice]:
        """Get list of available audio input devices."""
        return QMediaDevices.audioInputs()

    @staticmethod
    def get_default_audio_device() -> QAudioDevice | None:
        """Get the system default audio input device."""
        return QMediaDevices.defaultAudioInput()

    # Preview methods

    def start_preview(self) -> bool:
        """Start screen capture for live preview without recording.

        Only starts if screen capture permission is already granted.
        Call check_screen_capture_permission(request_if_needed=True) first
        if you want to prompt the user.
        """
        if self._config.audio_only:
            self._screen_capture.setActive(False)
            self._preview_active = False
            self._start_audio_monitoring()
            return False

        if self._preview_active:
            return True

        # Only check — do NOT request here to avoid prompting on every app start.
        if not has_screen_capture_access():
            print("[Screen] Screen capture permission not yet granted; skipping preview.")
            if not is_macos():
                return False
            print("[Screen] Attempting preview anyway (permission may update after TCC restart).")

        self._apply_config()
        self._screen_capture.setActive(True)

        # QScreenCapture.isActive() is unreliable immediately after setActive(True)
        # because the underlying AVFoundation session starts asynchronously.
        # We optimistically mark preview as active; _on_capture_error will clear it
        # if the hardware actually fails to start.
        self._preview_active = True

        # Start audio level monitoring
        self._start_audio_monitoring()
        return True

    def _suspend_preview_for_external_recording(self) -> None:
        """Stop the Qt preview while an external recorder owns screen capture."""
        self._resume_preview_after_external_recording = self._preview_active
        if self._preview_active:
            self._screen_capture.setActive(False)
            self._preview_active = False

    def _resume_preview_after_external_recording_if_needed(self) -> None:
        """Restore the preview that was suspended for native/FFmpeg recording."""
        if not self._resume_preview_after_external_recording:
            return

        self._resume_preview_after_external_recording = False
        self.start_preview()

    def stop_preview(self):
        """Stop screen capture preview.

        Does nothing if currently recording.
        """
        if self.is_recording:
            return

        self._screen_capture.setActive(False)
        self._preview_active = False

        # Stop audio monitoring
        self._stop_audio_monitoring()

    @property
    def is_preview_active(self) -> bool:
        """Check if preview is currently active."""
        return self._preview_active

    # Microphone permission handling

    def check_microphone_permission(self) -> bool:
        """Check and request microphone permission if needed.

        Returns:
            True if permission is granted (or already checked), False if denied or pending.
            If pending, requests permission and emits permission_status_changed later.
        """
        # If we've already successfully used audio, skip the check
        # (Terminal app permission doesn't always register with Qt's API)
        if self._permission_checked:
            return True

        permission = QMicrophonePermission()
        app = QCoreApplication.instance()
        status = app.checkPermission(permission)

        if status == Qt.PermissionStatus.Granted:
            print("[Audio] Microphone permission: GRANTED")
            self._permission_checked = True
            return True
        elif status == Qt.PermissionStatus.Undetermined:
            print("[Audio] Microphone permission: UNDETERMINED - requesting...")
            app.requestPermission(permission, self, self._on_permission_result)
            return False
        else:  # Denied
            # On macOS with Terminal, permission might show as "Denied" even when Terminal has access
            # Try anyway and let the audio system fail if it really doesn't have permission
            print("[Audio] Microphone permission status: DENIED (but trying anyway - Terminal may have access)")
            self._permission_checked = True  # Don't keep checking
            return True

    def request_microphone_permission(self) -> bool:
        """Explicitly request microphone permission for voice capture."""
        permission = QMicrophonePermission()
        app = QCoreApplication.instance()
        status = app.checkPermission(permission)

        if status == Qt.PermissionStatus.Granted:
            print("[Audio] Microphone permission: GRANTED")
            self._permission_checked = True
            self.permission_status_changed.emit(True)
            return True

        if status == Qt.PermissionStatus.Undetermined:
            print("[Audio] Microphone permission: UNDETERMINED - requesting...")
            app.requestPermission(permission, self, self._on_permission_result)
            return False

        print("[Audio] Microphone permission: DENIED")
        self.permission_status_changed.emit(False)
        return False

    def _screen_permission_message(self) -> str:
        """Build a macOS-specific screen access message."""
        if not is_macos():
            return "Screen capture permission is required to record."

        return (
            "macOS screen capture access is required before recording.\n\n"
            "Approve the macOS prompt. If no permission entry appears for this app, "
            "launching via Terminal or launch_gui.command usually assigns the permission "
            "to Terminal instead.\n\n"
            "If macOS system audio is enabled in the recorder, this permission also "
            "covers native screen and system audio recording."
        )

    def check_screen_capture_permission(self, request_if_needed: bool = False) -> bool:
        """Check and optionally request macOS screen capture access."""
        granted = has_screen_capture_access()
        if granted:
            self.screen_permission_status_changed.emit(True)
            return True

        if request_if_needed:
            granted = request_screen_capture_access()

        self.screen_permission_status_changed.emit(granted)
        return granted

    def request_recording_permissions(self) -> tuple[bool, bool]:
        """Request screen capture and microphone permissions for recording."""
        if self._config.audio_only:
            return True, self.request_microphone_permission()
        screen_granted = self.check_screen_capture_permission(request_if_needed=True)
        microphone_granted = self.request_microphone_permission()
        return screen_granted, microphone_granted

    def _on_permission_result(self, permission):
        """Handle permission request result."""
        app = QCoreApplication.instance()
        status = app.checkPermission(permission)
        granted = status == Qt.PermissionStatus.Granted
        print(f"[Audio] Permission result: {'GRANTED' if granted else 'DENIED'}")
        self.permission_status_changed.emit(granted)
        if granted and self._preview_active:
            # Now that we have permission, start audio monitoring
            self._start_audio_monitoring()

    # Audio level monitoring

    def _start_audio_monitoring(self):
        """Start monitoring audio input levels."""
        if not self._config.audio_enabled:
            self.audio_level_changed.emit(0.0)
            return

        if self._audio_source is not None:
            return  # Already monitoring

        # Check microphone permission first
        if not self.check_microphone_permission():
            print("[Audio] Waiting for microphone permission...")
            return  # Will retry when permission granted via callback

        # Get current audio device
        device = None
        if self._config.audio_device_id:
            for d in self.get_available_audio_devices():
                if d.id().data().decode() == self._config.audio_device_id:
                    device = d
                    break

        if device is None:
            device = QMediaDevices.defaultAudioInput()

        if device is None or device.isNull():
            print("[Audio] ERROR: No audio device available for monitoring")
            return

        print(f"[Audio] Monitoring device: {device.description()}")

        # Create audio format for monitoring
        format = QAudioFormat()
        format.setSampleRate(16000)  # Lower rate for monitoring
        format.setChannelCount(1)
        format.setSampleFormat(QAudioFormat.SampleFormat.Int16)

        # Check if format is supported
        if not device.isFormatSupported(format):
            print("[Audio] Requested format not supported, using device preferred format")
            format = device.preferredFormat()

        print(f"[Audio] Format: {format.sampleRate()}Hz, {format.channelCount()}ch")

        # Create audio source
        self._audio_source = QAudioSource(device, format)
        self._audio_source.setVolume(self._config.audio_volume)

        # Start capturing to get levels
        self._audio_io_device = self._audio_source.start()

        if self._audio_io_device is None:
            print("[Audio] ERROR: Failed to start audio source - got None for IO device")
            self._audio_source = None
            return

        print("[Audio] Audio monitoring started successfully")

        # Start timer to read levels
        self._level_timer.start(50)  # 20 Hz update rate

    def _stop_audio_monitoring(self):
        """Stop monitoring audio input levels."""
        self._level_timer.stop()

        if self._audio_source is not None:
            self._audio_source.stop()
            self._audio_source = None
            self._audio_io_device = None

    def _update_audio_level(self):
        """Read audio samples and calculate level."""
        if self._audio_io_device is None:
            return

        # Read available bytes
        bytes_ready = self._audio_io_device.bytesAvailable()
        if bytes_ready < 64:  # Need at least some samples
            return

        # Read up to 1024 bytes (512 samples at 16-bit)
        data = self._audio_io_device.read(min(bytes_ready, 1024))
        if not data:
            return

        # Calculate RMS level from 16-bit samples
        try:
            num_samples = len(data) // 2
            if num_samples == 0:
                return

            # Unpack as signed 16-bit integers
            samples = struct.unpack(f'<{num_samples}h', data)

            # Calculate RMS
            sum_squares = sum(s * s for s in samples)
            rms = (sum_squares / num_samples) ** 0.5

            # Normalize to 0.0-1.0 (16-bit max is 32767)
            level = min(1.0, rms / 32767.0 * 3.0)  # Scale up for visibility

            self.audio_level_changed.emit(level)

        except Exception:
            pass  # Ignore errors in level calculation

    def restart_audio_monitoring(self):
        """Restart audio monitoring with current device settings."""
        self._stop_audio_monitoring()
        if self._preview_active or self.is_recording or self._config.audio_only:
            self._start_audio_monitoring()
