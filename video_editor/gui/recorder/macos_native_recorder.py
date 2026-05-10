"""Native macOS recorder backed by a ScreenCaptureKit Swift helper."""

from __future__ import annotations

import json
import platform
import tempfile
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from .ffmpeg_recorder import FFMPEG
from ...runtime_paths import resolve_bundled_binary


def _log_recorder(message: str) -> None:
    """Append native recorder diagnostics without depending on app stdout."""
    try:
        log_dir = Path.home() / "Library" / "Logs" / "Video Editor"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with (log_dir / "recorder.log").open("a", encoding="utf-8") as log_file:
            log_file.write(f"{timestamp} {message}\n")
    except OSError:
        pass


class NativeMacOSRecorderState(Enum):
    """Recording state for the native macOS recorder."""

    IDLE = auto()
    STARTING = auto()
    RECORDING = auto()
    STOPPING = auto()


@dataclass(frozen=True)
class _NativeRecorderLaunchOptions:
    screen_index: int
    capture_system_audio: bool
    capture_microphone: bool
    microphone_name: str | None
    frame_rate: int
    sample_rate: int
    channel_count: int


class _NativeSegmentFinalizeWorker:
    """Join native recorder segments after macOS interrupts a capture."""

    def __init__(self, segments: list[Path], output_path: Path) -> None:
        self._segments = segments
        self._output_path = output_path

    def run(self) -> tuple[bool, Path | None, str]:
        existing_segments = [path for path in self._segments if path.exists()]
        if not existing_segments:
            return False, None, "No recording segments were created."

        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)

            if len(existing_segments) == 1:
                source = existing_segments[0]
                if source.resolve() != self._output_path.resolve():
                    if self._output_path.exists():
                        self._output_path.unlink()
                    shutil.copy2(source, self._output_path)
                return True, self._output_path, ""

            temp_output = self._output_path.with_name(
                f".{self._output_path.stem}_joining{self._output_path.suffix}"
            )
            if temp_output.exists():
                temp_output.unlink()

            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                suffix=".ffconcat",
                delete=False,
            ) as list_file:
                list_path = Path(list_file.name)
                for segment in existing_segments:
                    list_file.write(f"file '{self._quote_concat_path(segment)}'\n")

            cmd = [
                FFMPEG,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-map",
                "0",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(temp_output),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            try:
                list_path.unlink()
            except OSError:
                pass

            if result.returncode != 0 or not temp_output.exists():
                details = (result.stderr or result.stdout or "").strip()
                if details:
                    details = "\n".join(details.splitlines()[-8:])
                return False, None, details or "FFmpeg could not join recording segments."

            if self._output_path.exists():
                self._output_path.unlink()
            temp_output.replace(self._output_path)
            return True, self._output_path, ""
        except Exception as exc:
            return False, None, str(exc)

    @staticmethod
    def _quote_concat_path(path: Path) -> str:
        return str(path).replace("'", "'\\''")


class NativeMacOSRecorder(QObject):
    """Record a macOS display with native system audio support."""

    recording_started = Signal()
    recording_stopped = Signal(Path)
    recording_error = Signal(str)
    recording_warning = Signal(str)
    duration_changed = Signal(float)
    _helper_event = Signal(dict)
    _process_exited = Signal(int)
    _segments_finalized = Signal(bool, object, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._process: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._wait_thread: threading.Thread | None = None
        self._finalize_thread: threading.Thread | None = None
        self._state = NativeMacOSRecorderState.IDLE
        self._start_time = 0.0
        self._output_path: Path | None = None
        self._active_output_path: Path | None = None
        self._launch_options: _NativeRecorderLaunchOptions | None = None
        self._segment_dir: Path | None = None
        self._segment_paths: list[Path] = []
        self._pending_restart = False
        self._pending_finished_path: Path | None = None
        self._restart_count = 0
        self._started_emitted = False
        self._last_error = ""
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._terminal_event_seen = False
        self._stop_requested = False
        self._duration_timer = QTimer(self)
        self._duration_timer.timeout.connect(self._emit_duration)
        self._helper_event.connect(self._handle_event)
        self._process_exited.connect(self._on_process_exited)
        self._segments_finalized.connect(self._on_segments_finalized)

    @property
    def state(self) -> NativeMacOSRecorderState:
        return self._state

    @property
    def is_recording(self) -> bool:
        return self._state in (
            NativeMacOSRecorderState.STARTING,
            NativeMacOSRecorderState.RECORDING,
            NativeMacOSRecorderState.STOPPING,
        )

    @staticmethod
    def is_supported_platform() -> bool:
        """Return True when the current macOS can support the helper."""

        version = platform.mac_ver()[0]
        if not version:
            return False

        try:
            parts = version.split(".")
            major = int(parts[0])
            minor = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            return False

        return (major, minor) >= (15, 0)

    @staticmethod
    def _helper_source_path() -> Path:
        return Path(__file__).with_name("macos_system_audio_helper.swift")

    @staticmethod
    def _helper_binary_path() -> Path:
        return Path.home() / "Library" / "Caches" / "video_editor" / "macos_system_audio_helper"

    @staticmethod
    def _bundled_helper_path() -> Path | None:
        return resolve_bundled_binary(
            "macos_system_audio_helper",
            env_var="VIDEO_EDITOR_MACOS_HELPER_PATH",
        )

    @classmethod
    def ensure_helper_binary(cls) -> tuple[Path | None, str | None]:
        """Compile the helper if needed and return its path."""

        if not cls.is_supported_platform():
            return None, "Native macOS system audio capture requires macOS 15 or later."

        bundled_helper = cls._bundled_helper_path()
        if bundled_helper is not None:
            return bundled_helper, None

        swiftc = shutil.which("swiftc")
        if not swiftc:
            return None, "swiftc was not found. Install Xcode or the Xcode command line tools."

        source_path = cls._helper_source_path()
        if not source_path.exists():
            return None, f"Recorder helper source is missing: {source_path}"

        binary_path = cls._helper_binary_path()
        if binary_path.exists() and binary_path.stat().st_mtime >= source_path.stat().st_mtime:
            return binary_path, None

        binary_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            swiftc,
            "-parse-as-library",
            "-O",
            str(source_path),
            "-o",
            str(binary_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not binary_path.exists():
            details = (result.stderr or result.stdout or "").strip()
            if details:
                last_lines = "\n".join(details.splitlines()[-8:])
                return None, f"Failed to compile the native macOS recorder helper.\n\n{last_lines}"
            return None, "Failed to compile the native macOS recorder helper."

        return binary_path, None

    def start_recording(
        self,
        screen_index: int,
        output_path: Path,
        capture_system_audio: bool,
        capture_microphone: bool,
        microphone_name: str | None,
        frame_rate: int = 30,
        sample_rate: int = 48000,
        channel_count: int = 2,
    ) -> bool:
        """Start the native recorder helper."""

        if self._state != NativeMacOSRecorderState.IDLE:
            return False

        helper_path, helper_error = self.ensure_helper_binary()
        if helper_error or helper_path is None:
            self.recording_error.emit(helper_error or "Native recorder helper is unavailable.")
            return False

        self._output_path = output_path
        self._active_output_path = output_path
        self._launch_options = _NativeRecorderLaunchOptions(
            screen_index=screen_index,
            capture_system_audio=capture_system_audio,
            capture_microphone=capture_microphone,
            microphone_name=microphone_name,
            frame_rate=frame_rate,
            sample_rate=sample_rate,
            channel_count=channel_count,
        )
        self._segment_dir = None
        self._segment_paths = []
        self._pending_restart = False
        self._pending_finished_path = None
        self._restart_count = 0
        self._started_emitted = False
        self._last_error = ""
        self._stderr_tail.clear()
        self._terminal_event_seen = False
        self._stop_requested = False

        return self._start_helper_process(helper_path, output_path, reset_timer=True)

    def _start_helper_process(
        self,
        helper_path: Path,
        output_path: Path,
        *,
        reset_timer: bool,
    ) -> bool:
        launch_options = self._launch_options
        if launch_options is None:
            self.recording_error.emit("Native recorder launch options were not initialized.")
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(helper_path),
            "--display-index",
            str(launch_options.screen_index),
            "--output",
            str(output_path),
            "--capture-system-audio",
            "1" if launch_options.capture_system_audio else "0",
            "--capture-microphone",
            "1" if launch_options.capture_microphone else "0",
            "--frame-rate",
            str(launch_options.frame_rate),
            "--sample-rate",
            str(launch_options.sample_rate),
            "--channel-count",
            str(launch_options.channel_count),
        ]
        if launch_options.microphone_name:
            cmd += ["--microphone-name", launch_options.microphone_name]

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            self.recording_error.emit(str(exc))
            return False

        _log_recorder(f"started helper output={output_path}")
        self._state = NativeMacOSRecorderState.STARTING
        if reset_timer:
            self._start_time = time.time()
        self._stderr_tail.clear()
        self._terminal_event_seen = False
        self._active_output_path = output_path
        if not self._duration_timer.isActive():
            self._duration_timer.start(100)

        self._stdout_thread = threading.Thread(target=self._read_stdout, name="macos-recorder-stdout", daemon=True)
        self._stdout_thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, name="macos-recorder-stderr", daemon=True)
        self._stderr_thread.start()
        self._wait_thread = threading.Thread(target=self._wait_for_exit, name="macos-recorder-wait", daemon=True)
        self._wait_thread.start()
        return True

    def stop_recording(self) -> None:
        """Request a graceful stop."""

        if self._state not in (NativeMacOSRecorderState.STARTING, NativeMacOSRecorderState.RECORDING):
            return

        self._state = NativeMacOSRecorderState.STOPPING
        self._stop_requested = True

        if self._process and self._process.poll() is None and self._process.stdin:
            try:
                self._process.stdin.write("stop\n")
                self._process.stdin.flush()
            except Exception:
                pass
        elif self._pending_restart:
            self._pending_restart = False
            if self._pending_finished_path is not None:
                self._append_finished_segment(self._pending_finished_path)
                self._pending_finished_path = None
            self._finalize_after_stop()
            return

        def kill_later() -> None:
            process = self._process
            if process is None:
                return
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

        threading.Thread(target=kill_later, name="macos-recorder-stop-timeout", daemon=True).start()

    def _emit_duration(self) -> None:
        if self._state in (NativeMacOSRecorderState.STARTING, NativeMacOSRecorderState.RECORDING):
            self.duration_changed.emit(time.time() - self._start_time)

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._helper_event.emit(payload)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return

        for raw_line in process.stderr:
            line = raw_line.strip()
            if line:
                self._stderr_tail.append(line)
                _log_recorder(f"helper stderr: {line}")

    def _wait_for_exit(self) -> None:
        process = self._process
        if process is None:
            return

        return_code = process.wait()
        self._process_exited.emit(return_code)

    def _on_process_exited(self, return_code: int) -> None:
        _log_recorder(f"helper exited code={return_code} terminal_event_seen={self._terminal_event_seen}")
        if self._terminal_event_seen:
            pending_restart = self._pending_restart
            pending_finished_path = self._pending_finished_path
            self._process = None
            self._stdout_thread = None
            self._stderr_thread = None
            self._wait_thread = None
            if pending_restart:
                self._pending_restart = False
                self._pending_finished_path = None
                if self._stop_requested:
                    if pending_finished_path is not None:
                        self._append_finished_segment(pending_finished_path)
                    self._finalize_after_stop()
                else:
                    self._restart_after_interruption(pending_finished_path)
            return

        stop_requested = self._stop_requested
        self._duration_timer.stop()
        self._state = NativeMacOSRecorderState.IDLE
        self._stop_requested = False
        self._process = None
        self._stdout_thread = None
        self._stderr_thread = None
        self._wait_thread = None

        if return_code == 0 and self._output_path and self._output_path.exists():
            if stop_requested:
                self.recording_stopped.emit(self._output_path)
            else:
                self.recording_error.emit(
                    "Native macOS recorder exited before Stop was pressed."
                    f"{self._recording_file_details()}"
                )
            return

        details = self._last_error
        if not details and self._stderr_tail:
            details = self._stderr_tail[-1]
        if not details:
            details = f"Native macOS recorder exited unexpectedly with code {return_code}."
        self.recording_error.emit(details)

    def _handle_event(self, payload: dict) -> None:
        event = payload.get("event")
        if not event:
            return

        if event == "started":
            self._state = NativeMacOSRecorderState.RECORDING
            if not self._started_emitted:
                self._started_emitted = True
                self.recording_started.emit()
            return

        if event == "warning":
            message = str(payload.get("message", "")).strip()
            if message:
                self.recording_warning.emit(message)
            return

        if event == "error":
            self._terminal_event_seen = True
            self._state = NativeMacOSRecorderState.IDLE
            self._stop_requested = False
            self._last_error = str(payload.get("message", "Native macOS recorder failed.")).strip()
            _log_recorder(f"helper error: {self._last_error}")
            self._duration_timer.stop()
            self.recording_error.emit(self._last_error)
            return

        if event == "finished":
            self._terminal_event_seen = True
            output_path_raw = str(payload.get("output_path", "")).strip()
            output_path = Path(output_path_raw) if output_path_raw else None
            requested_stop = bool(payload.get("requested_stop")) or self._stop_requested
            interrupted = bool(payload.get("interrupted"))
            interruption_message = str(payload.get("message", "")).strip()
            _log_recorder(
                "helper finished "
                f"output={output_path} requested_stop={requested_stop} interrupted={interrupted}"
            )

            if interrupted and not self._stop_requested:
                self._pending_restart = True
                self._pending_finished_path = output_path or self._active_output_path
                _log_recorder(f"interruption pending restart message={interruption_message}")
                self.recording_warning.emit(
                    "macOS interrupted the ScreenCaptureKit stream. "
                    "Video Editor is saving that segment and restarting recording."
                    + (f"\n\n{interruption_message}" if interruption_message else "")
                )
                return

            self._state = NativeMacOSRecorderState.STOPPING
            self._stop_requested = False

            if not requested_stop:
                self._duration_timer.stop()
                self._state = NativeMacOSRecorderState.IDLE
                self.recording_error.emit(
                    "Native macOS recorder finished before Stop was pressed."
                    f"{self._recording_file_details(output_path)}"
                )
                return

            if output_path is not None and output_path.exists():
                if interrupted:
                    self.recording_warning.emit(
                        "macOS stopped the ScreenCaptureKit stream before Stop was pressed. "
                        "The partial recording was saved.\n\n"
                        f"{interruption_message or 'No additional macOS error detail was provided.'}"
                    )
                if self._segment_paths or output_path != self._output_path:
                    self._append_finished_segment(output_path)
                self._finalize_after_stop()
            elif self._output_path and self._output_path.exists():
                if interrupted:
                    self.recording_warning.emit(
                        "macOS stopped the ScreenCaptureKit stream before Stop was pressed. "
                        "The partial recording was saved.\n\n"
                        f"{interruption_message or 'No additional macOS error detail was provided.'}"
                    )
                if self._segment_paths:
                    self._append_finished_segment(self._output_path)
                self._finalize_after_stop()
            else:
                self._duration_timer.stop()
                self._state = NativeMacOSRecorderState.IDLE
                self.recording_error.emit("Native macOS recorder finished without creating an output file.")

    def _restart_after_interruption(self, finished_path: Path | None) -> None:
        if finished_path is not None:
            self._append_finished_segment(finished_path)

        if not self._segment_paths:
            self._duration_timer.stop()
            self._state = NativeMacOSRecorderState.IDLE
            self.recording_error.emit("macOS interrupted recording before a segment file was created.")
            return

        helper_path, helper_error = self.ensure_helper_binary()
        if helper_error or helper_path is None:
            self._duration_timer.stop()
            self._state = NativeMacOSRecorderState.IDLE
            self.recording_error.emit(helper_error or "Native recorder helper is unavailable.")
            return

        self._restart_count += 1
        next_output = self._next_segment_path()
        _log_recorder(f"restarting helper after interruption count={self._restart_count} output={next_output}")
        if not self._start_helper_process(helper_path, next_output, reset_timer=False):
            self._duration_timer.stop()
            self._state = NativeMacOSRecorderState.IDLE
            self.recording_error.emit(
                "macOS interrupted recording and Video Editor could not restart capture."
            )

    def _append_finished_segment(self, path: Path) -> None:
        if not path.exists():
            return
        segment_path = self._segment_path_for_finished_file(path)
        if segment_path not in self._segment_paths:
            self._segment_paths.append(segment_path)

    def _segment_path_for_finished_file(self, path: Path) -> Path:
        output_path = self._output_path
        if output_path is None or path != output_path:
            return path

        segment_dir = self._ensure_segment_dir()
        segment_path = segment_dir / f"part{len(self._segment_paths):03d}{path.suffix}"
        if segment_path.exists():
            segment_path.unlink()
        path.replace(segment_path)
        return segment_path

    def _ensure_segment_dir(self) -> Path:
        if self._segment_dir is not None:
            return self._segment_dir
        if self._output_path is None:
            raise RuntimeError("Native recorder output path was not initialized.")
        self._segment_dir = self._output_path.parent / f".{self._output_path.stem}_segments"
        self._segment_dir.mkdir(parents=True, exist_ok=True)
        return self._segment_dir

    def _next_segment_path(self) -> Path:
        segment_dir = self._ensure_segment_dir()
        return segment_dir / f"part{len(self._segment_paths):03d}{self._output_path.suffix if self._output_path else '.mp4'}"

    def _finalize_after_stop(self) -> None:
        self._duration_timer.stop()
        output_path = self._output_path
        if output_path is None:
            self._state = NativeMacOSRecorderState.IDLE
            self.recording_error.emit("Native macOS recorder stopped without an output path.")
            return

        if not self._segment_paths:
            if output_path.exists():
                self._state = NativeMacOSRecorderState.IDLE
                self.recording_stopped.emit(output_path)
            else:
                self._state = NativeMacOSRecorderState.IDLE
                self.recording_error.emit("Native macOS recorder finished without creating an output file.")
            return

        self._state = NativeMacOSRecorderState.STOPPING
        segments = list(self._segment_paths)
        worker = _NativeSegmentFinalizeWorker(segments, output_path)
        _log_recorder(f"joining {len(segments)} native recording segment(s) output={output_path}")

        def finalize() -> None:
            success, final_path, error = worker.run()
            self._segments_finalized.emit(success, final_path, error)

        self._finalize_thread = threading.Thread(target=finalize, name="macos-recorder-segment-finalize", daemon=True)
        self._finalize_thread.start()

    def _on_segments_finalized(self, success: bool, final_path_obj: object, error: str) -> None:
        self._finalize_thread = None
        self._state = NativeMacOSRecorderState.IDLE
        _log_recorder(f"segment finalize success={success} output={final_path_obj} error={error}")
        if success and isinstance(final_path_obj, Path):
            if self._restart_count:
                self.recording_warning.emit(
                    f"Recovered recording after {self._restart_count} macOS ScreenCaptureKit interruption"
                    f"{'s' if self._restart_count != 1 else ''}."
                )
            self.recording_stopped.emit(final_path_obj)
            return

        segment_list = "\n".join(str(path) for path in self._segment_paths)
        message = "Video Editor recorded segments but could not join them."
        if error:
            message += f"\n\nFFmpeg error:\n{error}"
        if segment_list:
            message += f"\n\nSegments saved at:\n{segment_list}"
        self.recording_error.emit(message)

    def _recording_file_details(self, output_path: Path | None = None) -> str:
        path = output_path or self._output_path
        if path is None:
            return "\n\nNo output path had been assigned yet."

        if path.exists():
            try:
                size_mb = path.stat().st_size / (1024 * 1024)
                return (
                    "\n\nPartial recording saved at:\n"
                    f"{path}\n"
                    f"Size: {size_mb:.1f} MB"
                )
            except OSError:
                return f"\n\nPartial recording saved at:\n{path}"

        return (
            "\n\nExpected output path:\n"
            f"{path}\n"
            "The file was not created."
        )
