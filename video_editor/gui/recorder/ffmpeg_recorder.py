"""FFmpeg-based screen recording with real-time crop."""

import threading
import signal
import subprocess
import time
import re
from pathlib import Path
from enum import Enum, auto

from PySide6.QtCore import QObject, Signal, QTimer


def _remux_to_mp4(
    input_path: Path,
    output_path: Path,
    audio_encoder: str | None,
    audio_sample_rate: int | None,
    audio_channels: int | None,
) -> tuple[bool, str | None]:
    """Remux a recording to MP4 (encode audio only if needed)."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-map",
            "0",
            "-c:v",
            "copy",
        ]
        if audio_encoder:
            cmd += ["-c:a", audio_encoder, "-b:a", "256k"]
            if audio_channels:
                cmd += ["-ac", str(audio_channels)]
            if audio_sample_rate:
                cmd += ["-ar", str(audio_sample_rate)]
        else:
            cmd += ["-c:a", "copy"]
        cmd += [
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and output_path.exists():
            return True, None
        print("[FFmpeg] Remux failed:")
        if result.stderr:
            last_line = result.stderr.strip().splitlines()[-1]
            print(last_line)
            return False, last_line
        return False, None
    except Exception as e:
        print(f"[FFmpeg] Remux error: {e}")
        return False, str(e)


class FFmpegRecorderState(Enum):
    """Recording state machine states."""
    IDLE = auto()
    RECORDING = auto()
    STOPPING = auto()


class _FFmpegFinalizeWorker:
    """Finalize an FFmpeg recording without blocking the UI."""

    def __init__(
        self,
        process: subprocess.Popen | None,
        output_path: Path | None,
        final_output_path: Path | None,
        remux_audio_encoder: str | None,
        remux_audio_sample_rate: int | None,
        remux_audio_channels: int | None,
    ) -> None:
        self._process = process
        self._output_path = output_path
        self._final_output_path = final_output_path
        self._remux_audio_encoder = remux_audio_encoder
        self._remux_audio_sample_rate = remux_audio_sample_rate
        self._remux_audio_channels = remux_audio_channels

    def run(self) -> tuple[bool, Path | None, str, str]:
        if self._process:
            try:
                # Attempt graceful stop first (may not work for avfoundation)
                self._process.send_signal(signal.SIGINT)
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Force terminate if graceful stop times out
                print("[FFmpeg] Graceful stop timed out, terminating...")
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except Exception:
                    self._process.kill()
            except Exception as e:
                print(f"[FFmpeg] Error stopping: {e}")
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except Exception:
                    self._process.kill()

        if self._output_path and self._output_path.exists():
            output_path = self._output_path
            warning = ""
            if self._final_output_path and self._final_output_path != self._output_path:
                remux_ok, remux_error = _remux_to_mp4(
                    self._output_path,
                    self._final_output_path,
                    self._remux_audio_encoder,
                    self._remux_audio_sample_rate,
                    self._remux_audio_channels,
                )
                if remux_ok:
                    output_path = self._final_output_path
                else:
                    print(f"[FFmpeg] Using raw recording at: {self._output_path}")
                    message = (
                        "Remux to MP4 failed. Raw recording saved at:\n"
                        f"{self._output_path}"
                    )
                    if remux_error:
                        message += f"\n\nFFmpeg error: {remux_error}"
                    warning = message
            return True, output_path, warning, ""
        return False, None, "", "Recording file not created"


class FFmpegRecorder(QObject):
    """Records screen using FFmpeg avfoundation with real-time crop.

    This recorder applies the crop filter during encoding rather than
    post-processing, which:
    - Reduces file size immediately (no full-screen intermediate)
    - Encodes smaller frames (faster)
    - Uses hardware encoding for the cropped output

    Signals:
        recording_started: Emitted when recording begins
        recording_stopped: Emitted when recording ends (path)
        recording_error: Emitted on error (error_string)
        duration_changed: Emitted with duration (seconds)
    """

    recording_started = Signal()
    recording_stopped = Signal(Path)
    recording_error = Signal(str)
    recording_warning = Signal(str)
    duration_changed = Signal(float)
    _finalize_done = Signal(bool, object, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: subprocess.Popen | None = None
        self._state = FFmpegRecorderState.IDLE
        self._output_path: Path | None = None
        self._final_output_path: Path | None = None
        self._remux_audio_encoder: str | None = None
        self._remux_audio_sample_rate: int | None = None
        self._remux_audio_channels: int | None = None
        self._finalize_thread: threading.Thread | None = None
        self._start_time: float = 0
        self._duration_timer = QTimer(self)
        self._duration_timer.timeout.connect(self._update_duration)
        self._finalize_done.connect(self._on_finalize_done)

    @property
    def state(self) -> FFmpegRecorderState:
        """Get current recorder state."""
        return self._state

    @property
    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self._state == FFmpegRecorderState.RECORDING

    def start_recording(
        self,
        screen_index: int,
        crop_rect: tuple[int, int, int, int],
        audio_device_index: int,
        output_path: Path,
        final_output_path: Path | None = None,
        audio_sample_rate: int | None = None,
        audio_channels: int | None = None,
        use_hardware: bool = True,
        framerate: int = 30
    ) -> bool:
        """Start recording with crop applied during encoding.

        Args:
            screen_index: Screen index for avfoundation
            crop_rect: Tuple of (x, y, width, height) for crop region
            audio_device_index: Audio device index for avfoundation
            output_path: Path for output file
            final_output_path: Optional final path for remux output (e.g., MP4)
            audio_sample_rate: Optional audio sample rate (Hz)
            audio_channels: Optional audio channel count
            use_hardware: Use VideoToolbox hardware encoding
            framerate: Recording framerate

        Returns:
            True if recording started successfully
        """
        if self._state != FFmpegRecorderState.IDLE:
            return False

        crop_x, crop_y, crop_w, crop_h = crop_rect

        # Encoder selection
        if use_hardware:
            encoder_args = [
                "-c:v", "h264_videotoolbox",
                "-q:v", "70",
                "-profile:v", "high",
                "-allow_sw", "true",
            ]
        else:
            encoder_args = [
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "20",
            ]

        # Build FFmpeg command
        output_ext = output_path.suffix.lower()
        container_args = []
        if output_ext in {".mp4", ".mov"}:
            # Use fragmented MP4 so the file remains playable even if FFmpeg
            # is force-killed (avfoundation can ignore SIGINT/STDIN).
            container_args = [
                "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            ]

        use_pcm_audio = output_ext == ".mkv" and final_output_path is not None
        if use_pcm_audio:
            audio_args = ["-c:a", "pcm_s16le"]
            self._remux_audio_encoder = self._get_best_aac_encoder()
            self._remux_audio_sample_rate = audio_sample_rate
            self._remux_audio_channels = audio_channels
        else:
            audio_encoder = self._get_best_aac_encoder()
            audio_args = [
                "-c:a", audio_encoder,
                "-b:a", "256k",
            ]
            self._remux_audio_encoder = None
            self._remux_audio_sample_rate = None
            self._remux_audio_channels = None
        if audio_channels:
            audio_args += ["-ac", str(audio_channels)]
        if audio_sample_rate:
            audio_args += ["-ar", str(audio_sample_rate)]

        cmd = [
            "ffmpeg",
            "-y",
            "-thread_queue_size", "1024",
            "-f", "avfoundation",
            "-framerate", str(framerate),
            "-capture_cursor", "1",
            "-i", f"{screen_index}:{audio_device_index}",
            "-vf", f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}",
            *encoder_args,
            *audio_args,
            *container_args,
            str(output_path),
        ]

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            self._state = FFmpegRecorderState.RECORDING
            self._output_path = output_path
            self._final_output_path = final_output_path or output_path
            self._start_time = time.time()
            self._duration_timer.start(100)  # Update every 100ms

            self.recording_started.emit()
            return True

        except Exception as e:
            self.recording_error.emit(str(e))
            return False

    @staticmethod
    def _get_best_aac_encoder() -> str:
        """Pick the best available AAC encoder on this FFmpeg build."""
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
            )
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            if "aac_at" in output:
                return "aac_at"
            if "libfdk_aac" in output:
                return "libfdk_aac"
        except Exception:
            pass
        return "aac"

    def stop_recording(self):
        """Stop recording gracefully."""
        if self._state != FFmpegRecorderState.RECORDING:
            return

        self._state = FFmpegRecorderState.STOPPING
        self._duration_timer.stop()

        worker = _FFmpegFinalizeWorker(
            self._process,
            self._output_path,
            self._final_output_path,
            self._remux_audio_encoder,
            self._remux_audio_sample_rate,
            self._remux_audio_channels,
        )

        def run_finalize() -> None:
            success, output_path, warning, error = worker.run()
            self._finalize_done.emit(success, output_path, warning, error)

        self._finalize_thread = threading.Thread(
            target=run_finalize,
            name="ffmpeg-finalize",
            daemon=True,
        )
        self._finalize_thread.start()

    def _on_finalize_done(self, success: bool, output_path_obj: object, warning: str, error: str) -> None:
        self._state = FFmpegRecorderState.IDLE
        self._process = None
        self._finalize_thread = None

        if warning:
            self.recording_warning.emit(warning)

        if success and isinstance(output_path_obj, Path):
            self.recording_stopped.emit(output_path_obj)
            return

        self.recording_error.emit(error or "Recording file not created")

    def _update_duration(self):
        """Update duration signal."""
        if self._state == FFmpegRecorderState.RECORDING:
            duration = time.time() - self._start_time
            self.duration_changed.emit(duration)

    @staticmethod
    def get_ffmpeg_audio_devices() -> list[tuple[int, str]]:
        """Get list of available audio input devices from FFmpeg.

        Returns:
            List of (index, name) tuples for audio devices
        """
        try:
            result = subprocess.run(
                ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                capture_output=True,
                text=True
            )

            # Parse audio device section from stderr
            lines = result.stderr.split('\n')
            audio_section = False
            audio_devices = []

            for line in lines:
                if "audio devices:" in line.lower():
                    audio_section = True
                    continue
                if audio_section:
                    # Match device lines like "[0] Built-in Microphone"
                    match = re.search(r'\[(\d+)\]\s+(.+)', line)
                    if match:
                        audio_devices.append((int(match.group(1)), match.group(2).strip()))
                    elif line.strip() and not line.strip().startswith('['):
                        # End of device list
                        break

            return audio_devices

        except Exception:
            return []
