"""Native macOS recorder backed by a ScreenCaptureKit Swift helper."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import threading
import time
from collections import deque
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from ...runtime_paths import resolve_bundled_binary


class NativeMacOSRecorderState(Enum):
    """Recording state for the native macOS recorder."""

    IDLE = auto()
    STARTING = auto()
    RECORDING = auto()
    STOPPING = auto()


class NativeMacOSRecorder(QObject):
    """Record a macOS display with native system audio support."""

    recording_started = Signal()
    recording_stopped = Signal(Path)
    recording_error = Signal(str)
    recording_warning = Signal(str)
    duration_changed = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._process: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._wait_thread: threading.Thread | None = None
        self._state = NativeMacOSRecorderState.IDLE
        self._start_time = 0.0
        self._output_path: Path | None = None
        self._last_error = ""
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._terminal_event_seen = False
        self._duration_timer = QTimer(self)
        self._duration_timer.timeout.connect(self._emit_duration)

    @property
    def state(self) -> NativeMacOSRecorderState:
        return self._state

    @property
    def is_recording(self) -> bool:
        return self._state in (NativeMacOSRecorderState.STARTING, NativeMacOSRecorderState.RECORDING)

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

        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(helper_path),
            "--display-index",
            str(screen_index),
            "--output",
            str(output_path),
            "--capture-system-audio",
            "1" if capture_system_audio else "0",
            "--capture-microphone",
            "1" if capture_microphone else "0",
            "--frame-rate",
            str(frame_rate),
            "--sample-rate",
            str(sample_rate),
            "--channel-count",
            str(channel_count),
        ]
        if microphone_name:
            cmd += ["--microphone-name", microphone_name]

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

        self._state = NativeMacOSRecorderState.STARTING
        self._start_time = time.time()
        self._output_path = output_path
        self._last_error = ""
        self._stderr_tail.clear()
        self._terminal_event_seen = False
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
        self._duration_timer.stop()

        if self._process and self._process.poll() is None and self._process.stdin:
            try:
                self._process.stdin.write("stop\n")
                self._process.stdin.flush()
            except Exception:
                pass

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
            self._handle_event(payload)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return

        for raw_line in process.stderr:
            line = raw_line.strip()
            if line:
                self._stderr_tail.append(line)

    def _wait_for_exit(self) -> None:
        process = self._process
        if process is None:
            return

        return_code = process.wait()
        self._duration_timer.stop()

        if self._terminal_event_seen:
            self._process = None
            self._stdout_thread = None
            self._stderr_thread = None
            self._wait_thread = None
            return

        self._state = NativeMacOSRecorderState.IDLE
        self._process = None
        self._stdout_thread = None
        self._stderr_thread = None
        self._wait_thread = None

        if return_code == 0 and self._output_path and self._output_path.exists():
            self.recording_stopped.emit(self._output_path)
            return

        details = self._last_error
        if not details and self._stderr_tail:
            details = self._stderr_tail[-1]
        if not details:
            details = "Native macOS recorder exited unexpectedly."
        self.recording_error.emit(details)

    def _handle_event(self, payload: dict) -> None:
        event = payload.get("event")
        if not event:
            return

        if event == "started":
            self._state = NativeMacOSRecorderState.RECORDING
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
            self._last_error = str(payload.get("message", "Native macOS recorder failed.")).strip()
            self._duration_timer.stop()
            self.recording_error.emit(self._last_error)
            return

        if event == "finished":
            self._terminal_event_seen = True
            self._state = NativeMacOSRecorderState.IDLE
            self._duration_timer.stop()
            output_path_raw = str(payload.get("output_path", "")).strip()
            output_path = Path(output_path_raw) if output_path_raw else None
            if output_path is not None and output_path.exists():
                self.recording_stopped.emit(output_path)
            elif self._output_path and self._output_path.exists():
                self.recording_stopped.emit(self._output_path)
            else:
                self.recording_error.emit("Native macOS recorder finished without creating an output file.")
