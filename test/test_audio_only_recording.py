from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from video_editor.gui.recorder.recording_controller import (
    RecordingController,
    RecordingState,
)


class _RecorderDouble:
    def __init__(self) -> None:
        self.media_format = None
        self.quality = None
        self.sample_rate = None
        self.channel_count = None
        self.bit_rate = None
        self.output_location = None
        self.record_calls = 0

    def setMediaFormat(self, media_format) -> None:
        self.media_format = media_format

    def setQuality(self, quality) -> None:
        self.quality = quality

    def setAudioSampleRate(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate

    def setAudioChannelCount(self, channel_count: int) -> None:
        self.channel_count = channel_count

    def setAudioBitRate(self, bit_rate: int) -> None:
        self.bit_rate = bit_rate

    def audioSampleRate(self) -> int | None:
        return self.sample_rate

    def audioChannelCount(self) -> int | None:
        return self.channel_count

    def audioBitRate(self) -> int | None:
        return self.bit_rate

    def setOutputLocation(self, output_location) -> None:
        self.output_location = output_location

    def record(self) -> None:
        self.record_calls += 1


class _SessionDouble:
    def __init__(self) -> None:
        self.screen_capture = object()

    def setScreenCapture(self, screen_capture) -> None:
        self.screen_capture = screen_capture


class _ScreenCaptureDouble:
    def __init__(self) -> None:
        self.active = True

    def setActive(self, active: bool) -> None:
        self.active = active


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_audio_only_format_requests_high_quality_aac(qt_app):
    controller = RecordingController()
    recorder = _RecorderDouble()
    controller._recorder = recorder

    controller._configure_audio_only_recorder_format()

    assert recorder.media_format is not None
    assert recorder.sample_rate == 48000
    assert recorder.channel_count == 2
    assert recorder.bit_rate == 256000


def test_audio_only_recording_uses_native_qt_capture_path(qt_app, monkeypatch, tmp_path: Path):
    controller = RecordingController()
    recorder = _RecorderDouble()
    session = _SessionDouble()
    screen_capture = _ScreenCaptureDouble()
    output_path = tmp_path / "recording.m4a"
    calls: list[str] = []

    controller._recorder = recorder
    controller._session = session
    controller._screen_capture = screen_capture
    controller._config.audio_only = True
    controller._preview_active = True
    monkeypatch.setattr(controller, "_stop_audio_monitoring", lambda: calls.append("stop_monitoring"))
    monkeypatch.setattr(controller, "_apply_config", lambda: calls.append("apply_config"))
    monkeypatch.setattr(
        controller,
        "_configure_audio_only_recorder_format",
        lambda: calls.append("configure_audio"),
    )
    monkeypatch.setattr(controller, "_get_output_path", lambda: output_path)

    assert controller._start_audio_only_recording() is True

    assert calls == ["stop_monitoring", "apply_config", "configure_audio"]
    assert screen_capture.active is False
    assert session.screen_capture is None
    assert recorder.output_location.toLocalFile() == str(output_path)
    assert recorder.record_calls == 1
    assert controller.state is RecordingState.RECORDING
    assert controller._audio_only_recorder_active is True

    controller._restore_after_audio_only_recording()

    assert session.screen_capture is screen_capture
    assert controller._audio_only_recorder_active is False
    assert controller._resume_preview_after_audio_only_recording is False
