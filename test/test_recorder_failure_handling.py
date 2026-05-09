from pathlib import Path

from PySide6.QtCore import QCoreApplication

from video_editor.gui.recorder.macos_native_recorder import NativeMacOSRecorder


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
