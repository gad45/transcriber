from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

import video_editor.gui.recorder.macos_native_recorder as native_recorder_module
from video_editor.gui.recorder.macos_native_recorder import NativeMacOSRecorder


@pytest.fixture(autouse=True)
def _disable_recorder_log(monkeypatch):
    monkeypatch.setattr(native_recorder_module, "_log_recorder", lambda message: None)


def _ensure_qt_app() -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is not None:
        return app
    return QCoreApplication([])


def test_native_recorder_finished_without_stop_is_an_error(tmp_path: Path):
    _ensure_qt_app()
    output_path = tmp_path / "recording.mp4"
    output_path.write_bytes(b"partial")

    recorder = NativeMacOSRecorder()
    errors: list[str] = []
    stopped: list[Path] = []
    recorder.recording_error.connect(errors.append)
    recorder.recording_stopped.connect(stopped.append)

    recorder._output_path = output_path
    recorder._handle_event({"event": "finished", "output_path": str(output_path)})

    assert stopped == []
    assert len(errors) == 1
    assert "before Stop was pressed" in errors[0]
    assert str(output_path) in errors[0]


def test_native_recorder_finished_after_stop_is_success(tmp_path: Path):
    _ensure_qt_app()
    output_path = tmp_path / "recording.mp4"
    output_path.write_bytes(b"complete")

    recorder = NativeMacOSRecorder()
    errors: list[str] = []
    stopped: list[Path] = []
    recorder.recording_error.connect(errors.append)
    recorder.recording_stopped.connect(stopped.append)

    recorder._output_path = output_path
    recorder._stop_requested = True
    recorder._handle_event({"event": "finished", "output_path": str(output_path)})

    assert errors == []
    assert stopped == [output_path]


def test_native_recorder_interruption_restarts_instead_of_stopping(tmp_path: Path):
    _ensure_qt_app()
    output_path = tmp_path / "recording.mp4"
    output_path.write_bytes(b"partial")

    recorder = NativeMacOSRecorder()
    errors: list[str] = []
    stopped: list[Path] = []
    warnings: list[str] = []
    restarted: list[Path | None] = []
    recorder.recording_error.connect(errors.append)
    recorder.recording_stopped.connect(stopped.append)
    recorder.recording_warning.connect(warnings.append)

    recorder._output_path = output_path
    recorder._active_output_path = output_path
    recorder._restart_after_interruption = restarted.append
    recorder._handle_event(
        {
            "event": "finished",
            "output_path": str(output_path),
            "requested_stop": True,
            "interrupted": True,
            "message": "ScreenCaptureKit stream stopped unexpectedly",
        }
    )

    assert errors == []
    assert stopped == []
    assert len(warnings) == 1
    assert recorder._pending_restart is True

    recorder._on_process_exited(0)

    assert restarted == [output_path]
    assert recorder._pending_restart is False
    assert errors == []
    assert stopped == []


def test_native_recorder_archives_first_segment_for_recovery(tmp_path: Path):
    _ensure_qt_app()
    output_path = tmp_path / "recording.mp4"
    output_path.write_bytes(b"first segment")

    recorder = NativeMacOSRecorder()
    recorder._output_path = output_path

    recorder._append_finished_segment(output_path)

    assert output_path.exists() is False
    assert len(recorder._segment_paths) == 1
    assert recorder._segment_paths[0].name == "part000.mp4"
    assert recorder._segment_paths[0].read_bytes() == b"first segment"
    assert recorder._next_segment_path().name == "part001.mp4"
