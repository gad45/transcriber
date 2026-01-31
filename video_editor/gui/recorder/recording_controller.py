"""Controller for screen and audio recording using Qt6 multimedia."""

from datetime import datetime
from pathlib import Path
from enum import Enum, auto

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
    duration_changed = Signal(int)  # milliseconds
    state_changed = Signal(RecordingState)
    audio_level_changed = Signal(float)  # 0.0-1.0
    permission_status_changed = Signal(bool)  # True if microphone permission granted

    def __init__(self, parent=None):
        super().__init__(parent)

        self._config = RecordingConfig()
        self._state = RecordingState.IDLE
        self._output_path: Path | None = None
        self._preview_active = False
        self._permission_checked = False  # Skip repeated permission checks

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

    def _setup_capture_session(self):
        """Initialize the capture session with components."""
        self._session.setScreenCapture(self._screen_capture)
        self._session.setRecorder(self._recorder)

        # Configure recorder format
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
        self._set_state(RecordingState.IDLE)
        # Resume audio monitoring for preview
        if self._preview_active:
            self._start_audio_monitoring()
        self.recording_error.emit(error_string)

    def _on_capture_error(self, error, error_string: str):
        """Handle screen capture errors."""
        self._set_state(RecordingState.IDLE)
        # Resume audio monitoring for preview
        if self._preview_active:
            self._start_audio_monitoring()
        self.recording_error.emit(f"Screen capture error: {error_string}")

    def _on_recorder_state_changed(self, state):
        """Handle recorder state changes."""
        if state == QMediaRecorder.RecorderState.StoppedState:
            if self._state == RecordingState.RECORDING:
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
            # Check if cropping is needed (resolution or aspect ratio is set)
            needs_crop = (
                not self._config.capture_full_screen and
                (self._config.target_resolution is not None or self._config.target_aspect_ratio is not None)
            )
            self._set_state(RecordingState.IDLE)
            # Resume audio monitoring for preview
            if self._preview_active:
                self._start_audio_monitoring()
            self.recording_stopped.emit(self._output_path, needs_crop)
        else:
            self._set_state(RecordingState.IDLE)
            # Resume audio monitoring for preview
            if self._preview_active:
                self._start_audio_monitoring()
            self.recording_error.emit("Recording file not found")

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
        self._config = config
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

        Raw recordings are saved to a 'raw' subdirectory to ensure they are
        never lost during post-processing (cropping). The raw files are kept
        even after processing, so users can always recover the original.
        """
        # Determine base output directory
        if self._config.output_directory:
            base_dir = Path(self._config.output_directory)
        else:
            movies_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
            base_dir = Path(movies_path) / "Recordings"

        # Raw recordings go in a subdirectory - never deleted automatically
        raw_dir = base_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self._config.filename_pattern.format(timestamp=timestamp)
        filename = f"{filename}.{self._config.container_format}"

        print(f"[Recording] Raw file will be saved to: {raw_dir / filename}")
        return raw_dir / filename

    def start_recording(self) -> bool:
        """Start recording with current configuration.

        Returns:
            True if recording started successfully, False otherwise
        """
        if self._state != RecordingState.IDLE:
            return False

        try:
            # Stop audio monitoring to release the device for recording
            self._stop_audio_monitoring()

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

    def stop_recording(self):
        """Stop the current recording."""
        if self._state not in (RecordingState.RECORDING, RecordingState.PAUSED):
            return

        self._recorder.stop()
        self._screen_capture.setActive(False)

    def pause_recording(self):
        """Pause the current recording."""
        if self._state != RecordingState.RECORDING:
            return

        self._recorder.pause()
        self._set_state(RecordingState.PAUSED)

    def resume_recording(self):
        """Resume a paused recording."""
        if self._state != RecordingState.PAUSED:
            return

        self._recorder.record()
        self._set_state(RecordingState.RECORDING)

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
        self._config.audio_enabled = enabled
        if enabled:
            self._setup_audio_input()
        else:
            self._session.setAudioInput(None)
            self._audio_input = None

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

    @staticmethod
    def get_available_screens() -> list[QScreen]:
        """Get list of available screens for capture."""
        return QGuiApplication.screens()

    @staticmethod
    def get_available_audio_devices() -> list[QAudioDevice]:
        """Get list of available audio input devices."""
        return QMediaDevices.audioInputs()

    @staticmethod
    def get_default_audio_device() -> QAudioDevice | None:
        """Get the system default audio input device."""
        return QMediaDevices.defaultAudioInput()

    # Preview methods

    def start_preview(self):
        """Start screen capture for live preview without recording.

        This allows users to see what will be recorded and position
        the crop overlay before starting the actual recording.
        """
        if self._preview_active:
            return

        self._apply_config()
        self._screen_capture.setActive(True)
        self._preview_active = True

        # Start audio level monitoring
        self._start_audio_monitoring()

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
        if self._preview_active or self.is_recording:
            self._start_audio_monitoring()
