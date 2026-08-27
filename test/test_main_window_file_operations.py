import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox

from video_editor.gui.file_dialogs import (
    MEDIA_FILE_FILTER,
    suggested_project_path,
    with_project_extension,
)
from video_editor.gui.main_window import MainWindow


_APP: QApplication | None = None


def _qt_app() -> QApplication:
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


class _Session:
    def __init__(self, media_path: Path, error: Exception | None = None):
        self.video_path = media_path
        self.error = error
        self.saved_paths: list[Path] = []

    def save(self, path: Path) -> None:
        if self.error:
            raise self.error
        self.saved_paths.append(path)
        path.write_text("saved", encoding="utf-8")


def _window_with_session(session: _Session) -> QMainWindow:
    _qt_app()
    window = QMainWindow()
    window._session = session
    window._project_path = None
    window._unsaved_changes = True
    return window


def test_media_filter_accepts_common_audio_only_files():
    for extension in ("*.m4a", "*.aac", "*.wav", "*.mp3", "*.flac", "*.ogg"):
        assert extension in MEDIA_FILE_FILTER


def test_project_path_defaults_beside_the_source_media(tmp_path: Path):
    media_path = tmp_path / "recording.m4a"

    assert suggested_project_path(media_path, tmp_path / "fallback") == (
        tmp_path / "recording.vedproj"
    )


def test_project_path_never_defaults_to_filesystem_root(tmp_path: Path):
    root_media = Path(Path.cwd().anchor) / "recording.m4a"

    assert suggested_project_path(root_media, tmp_path) == tmp_path / "recording.vedproj"


def test_project_extension_is_added_case_insensitively():
    assert with_project_extension("video2") == Path("video2.vedproj")
    assert with_project_extension("video2.VEDPROJ") == Path("video2.VEDPROJ")


def test_open_dialog_uses_audio_capable_filter(monkeypatch, tmp_path: Path):
    selected = tmp_path / "recording.m4a"
    captured = {}

    def fake_dialog(parent, title, directory, file_filter):
        captured.update(title=title, file_filter=file_filter)
        return str(selected), ""

    class Window:
        def _load_video(self, path):
            self.loaded_path = path

    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake_dialog)
    window = Window()

    MainWindow._open_video(window)

    assert captured["title"] == "Open Media"
    assert "*.m4a" in captured["file_filter"]
    assert window.loaded_path == selected


def test_loading_new_media_clears_an_old_project_destination(tmp_path: Path):
    media_path = tmp_path / "recording.m4a"
    media_path.touch()

    class Control:
        def setEnabled(self, enabled):
            self.enabled = enabled

        def setText(self, text):
            self.text = text

    class Player:
        def load_video(self, path):
            self.loaded_path = path

        def set_crop_config(self, config):
            self.crop_config = config

    class Window:
        _video_player = Player()
        _process_btn = Control()
        _status_label = Control()
        _project_path = tmp_path / "old-project.vedproj"
        _preview_generation = 0
        _preview_path = None

        def setWindowTitle(self, title):
            self.title = title

        def _cleanup_preview_path(self):
            MainWindow._cleanup_preview_path(self)

    window = Window()

    MainWindow._load_video(window, media_path)

    assert window._session.video_path == media_path
    assert window._video_player.loaded_path == media_path
    assert window._project_path is None


def test_save_as_defaults_beside_media_and_confirms_success(monkeypatch, tmp_path: Path):
    media_path = tmp_path / "recording.m4a"
    session = _Session(media_path)
    window = _window_with_session(session)
    captured = {}

    def fake_dialog(parent, title, suggested, file_filter):
        captured["suggested"] = suggested
        return str(tmp_path / "video2"), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", fake_dialog)
    window._suggested_project_path = lambda: MainWindow._suggested_project_path(window)
    window._save_project_to = lambda path: MainWindow._save_project_to(window, path)

    MainWindow._save_project_as(window)

    saved_path = tmp_path / "video2.vedproj"
    assert captured["suggested"] == str(tmp_path / "recording.vedproj")
    assert session.saved_paths == [saved_path]
    assert saved_path.exists()
    assert window._project_path == saved_path
    assert window._unsaved_changes is False


def test_save_failure_is_reported_and_does_not_mark_project_saved(monkeypatch, tmp_path: Path):
    session = _Session(tmp_path / "recording.m4a", PermissionError("not writable"))
    window = _window_with_session(session)
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda parent, title, message: messages.append((title, message)),
    )

    result = MainWindow._save_project_to(window, tmp_path / "video2.vedproj")

    assert result is False
    assert window._project_path is None
    assert window._unsaved_changes is True
    assert messages == [
        (
            "Project Save Failed",
            f"The project could not be saved to:\n{tmp_path / 'video2.vedproj'}\n\nnot writable",
        )
    ]


def test_preview_button_starts_rendering_the_edited_session():
    class Button:
        def setChecked(self, checked):
            self.checked = checked

    class Window:
        _session = object()
        _view_original_btn = Button()
        _view_preview_btn = Button()
        preview_started = False

        def _start_preview_render(self):
            self.preview_started = True

    window = Window()

    MainWindow._on_view_preview(window)

    assert window._view_original_btn.checked is False
    assert window._view_preview_btn.checked is True
    assert window.preview_started is True


def test_completed_preview_replaces_the_original_media_in_player(tmp_path: Path):
    old_preview = tmp_path / "old.m4a"
    new_preview = tmp_path / "edited.m4a"
    old_preview.touch()
    new_preview.touch()

    class Button:
        def setEnabled(self, enabled):
            self.enabled = enabled

    class Player:
        def load_video(self, path):
            self.loaded_path = path

    class Status:
        def setText(self, text):
            self.text = text

    class Window:
        _preview_generation = 3
        _preview_thread = object()
        _preview_path = old_preview
        _session = object()
        _view_preview_btn = Button()
        _video_player = Player()
        _status_label = Status()

        def _cleanup_preview_path(self):
            MainWindow._cleanup_preview_path(self)

    window = Window()

    MainWindow._on_preview_finished(window, True, (3, new_preview))

    assert old_preview.exists() is False
    assert window._preview_path == new_preview
    assert window._video_player.loaded_path == new_preview
    assert window._view_preview_btn.enabled is True
    assert "Previewing edited media" in window._status_label.text
