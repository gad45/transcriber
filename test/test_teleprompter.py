import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QFormLayout

from video_editor.gui.recorder.recording_settings import RecordingSettingsPanel
from video_editor.gui.recorder.teleprompter import TeleprompterView


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_teleprompter_uses_configured_reading_speed_and_can_pause():
    _qt_app()
    teleprompter = TeleprompterView()

    teleprompter.set_script("First line.\nSecond line.\nThird line.")
    teleprompter.set_reading_speed(90)

    assert teleprompter.reading_speed == 90
    assert teleprompter.start() is True
    assert teleprompter.is_scrolling is True

    teleprompter.pause()

    assert teleprompter.is_scrolling is False


def test_teleprompter_uses_selected_text_size():
    _qt_app()
    teleprompter = TeleprompterView()

    teleprompter.set_text_size(18)

    assert teleprompter.text_size == 18
    assert teleprompter._script_view.font().pointSize() == 18


def test_teleprompter_calibrates_scroll_rate_to_reading_speed():
    app = _qt_app()
    teleprompter = TeleprompterView()
    teleprompter.resize(800, 500)
    teleprompter.set_script("word " * 400)
    teleprompter.show()
    app.processEvents()

    teleprompter.set_reading_speed(60)
    slow_rate = teleprompter._scroll_pixels_per_second()
    teleprompter.set_reading_speed(120)
    normal_rate = teleprompter._scroll_pixels_per_second()

    assert slow_rate > 0
    assert normal_rate == pytest.approx(slow_rate * 2)


def test_teleprompter_reading_speed_control_emits_an_exact_wpm_value():
    _qt_app()
    settings = RecordingSettingsPanel()
    speeds = []
    settings.teleprompter_speed_changed.connect(speeds.append)

    settings._teleprompter_enabled_check.setChecked(True)
    settings._teleprompter_speed_spin.setValue(75)

    assert settings._teleprompter_speed_spin.suffix() == " wpm"
    assert speeds[-1] == 75


def test_settings_forms_wrap_fields_in_a_narrow_sidebar():
    app = _qt_app()
    settings = RecordingSettingsPanel()
    settings.resize(320, 1200)
    settings.show()
    settings._audio_only_check.setChecked(True)
    settings._teleprompter_enabled_check.setChecked(True)
    app.processEvents()

    forms = settings.findChildren(QFormLayout)
    assert forms
    assert all(
        form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapLongRows
        for form in forms
    )

    if settings._macos_audio_hint is not None:
        assert (
            settings._audio_device_combo.geometry().bottom()
            < settings._macos_audio_hint.geometry().top()
        )

    assert (
        settings._teleprompter_text_size_combo.geometry().bottom()
        < settings._teleprompter_speed_spin.geometry().top()
    )

    settings.close()


def test_teleprompter_requires_a_script_before_scrolling():
    _qt_app()
    teleprompter = TeleprompterView()

    assert teleprompter.start() is False
    assert teleprompter.is_scrolling is False
