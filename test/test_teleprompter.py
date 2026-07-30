import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from video_editor.gui.recorder.teleprompter import TeleprompterView


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_teleprompter_uses_configured_speed_and_can_pause():
    _qt_app()
    teleprompter = TeleprompterView()

    teleprompter.set_script("First line.\nSecond line.\nThird line.")
    teleprompter.set_scroll_speed(72)

    assert teleprompter.scroll_speed == 72
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


def test_teleprompter_requires_a_script_before_scrolling():
    _qt_app()
    teleprompter = TeleprompterView()

    assert teleprompter.start() is False
    assert teleprompter.is_scrolling is False
