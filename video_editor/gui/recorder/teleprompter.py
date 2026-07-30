"""On-screen teleprompter for audio-only recording."""

import re

from PySide6.QtCore import QElapsedTimer, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class TeleprompterView(QWidget):
    """Display a script and smoothly scroll it at a configurable speed."""

    scrolling_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("teleprompterView")

        self._script = ""
        self._reading_speed = 120
        self._text_size = 27
        self._scroll_remainder = 0.0
        self._elapsed = QElapsedTimer()
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(16)
        self._scroll_timer.timeout.connect(self._advance_scroll)

        self._setup_ui()
        self._show_placeholder()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("Teleprompter")
        title.setObjectName("teleprompterTitle")
        header.addWidget(title)
        header.addStretch()
        self._speed_label = QLabel()
        self._speed_label.setObjectName("teleprompterSpeed")
        header.addWidget(self._speed_label)
        layout.addLayout(header)

        self._script_view = QPlainTextEdit()
        self._script_view.setReadOnly(True)
        self._script_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._script_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._script_view.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._script_view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._script_view.setFont(QFont("Avenir Next", self._text_size))
        self._script_view.setStyleSheet(
            "QPlainTextEdit {"
            "background: #171717; color: #f4f4f4; border: 1px solid #3d3d3d; "
            "border-radius: 10px; padding: 56px 84px; line-height: 1.45;"
            "}"
        )
        layout.addWidget(self._script_view, 1)

        controls = QHBoxLayout()
        self._status_label = QLabel("Ready")
        self._status_label.setObjectName("teleprompterStatus")
        controls.addWidget(self._status_label)
        controls.addStretch()

        self._restart_button = QPushButton("Restart")
        self._play_pause_button = QPushButton("Start scrolling")
        self._play_pause_button.setObjectName("teleprompterPlayPause")
        controls.addWidget(self._restart_button)
        controls.addWidget(self._play_pause_button)
        layout.addLayout(controls)

        self.setStyleSheet(
            "QWidget#teleprompterView { background: #1f1f1f; }"
            "QLabel#teleprompterTitle { font-size: 20px; font-weight: bold; color: #f4f4f4; }"
            "QLabel#teleprompterSpeed, QLabel#teleprompterStatus { color: #aaa; font-size: 14px; }"
            "QPushButton { background: #3b3b3b; color: white; border: 1px solid #555; "
            "border-radius: 4px; padding: 7px 14px; }"
            "QPushButton:hover { background: #505050; }"
            "QPushButton#teleprompterPlayPause { background: #1976d2; border: none; }"
            "QPushButton#teleprompterPlayPause:hover { background: #1e88e5; }"
        )

        self._restart_button.clicked.connect(self.reset)
        self._play_pause_button.clicked.connect(self.toggle_scrolling)
        self._update_speed_label()

    @property
    def reading_speed(self) -> int:
        """Get the configured script pace in words per minute."""
        return self._reading_speed

    @property
    def text_size(self) -> int:
        """Get the script font size in points."""
        return self._text_size

    @property
    def is_scrolling(self) -> bool:
        """Return whether the script is scrolling currently."""
        return self._scroll_timer.isActive()

    def set_script(self, script: str) -> None:
        """Set the script to show in the teleprompter."""
        self.pause()
        self._script = script
        if script.strip():
            self._script_view.setPlainText(script)
            self._status_label.setText("Ready")
        else:
            self._show_placeholder()
        self._reset_scroll_position()

    def set_reading_speed(self, words_per_minute: int) -> None:
        """Set the target script pace in words per minute."""
        self._reading_speed = max(40, min(240, words_per_minute))
        self._update_speed_label()

    def set_text_size(self, points: int) -> None:
        """Set the script font size while preserving the current reading position."""
        self._text_size = max(14, min(48, points))
        self._script_view.setFont(QFont("Avenir Next", self._text_size))

    def start(self) -> bool:
        """Begin scrolling, returning False when there is no script to read."""
        if not self._script.strip():
            self._status_label.setText("Add a script in the Teleprompter settings")
            return False

        scrollbar = self._script_view.verticalScrollBar()
        if scrollbar.value() >= scrollbar.maximum():
            self._reset_scroll_position()

        self._scroll_remainder = 0.0
        self._elapsed.start()
        self._scroll_timer.start()
        self._status_label.setText("Scrolling")
        self._play_pause_button.setText("Pause scrolling")
        self.scrolling_changed.emit(True)
        return True

    def pause(self) -> None:
        """Pause automatic scrolling without changing the current position."""
        if not self._scroll_timer.isActive():
            return

        self._scroll_timer.stop()
        self._status_label.setText("Paused")
        self._play_pause_button.setText("Resume scrolling")
        self.scrolling_changed.emit(False)

    def reset(self) -> None:
        """Pause and rewind the script to its first line."""
        self.pause()
        self._reset_scroll_position()
        if self._script.strip():
            self._status_label.setText("Ready")
        self._play_pause_button.setText("Start scrolling")

    def toggle_scrolling(self) -> None:
        """Start or pause the teleprompter from its on-screen control."""
        if self.is_scrolling:
            self.pause()
        else:
            self.start()

    def _advance_scroll(self) -> None:
        elapsed_ms = self._elapsed.restart()
        if elapsed_ms <= 0:
            return

        self._scroll_remainder += (
            self._scroll_units_per_second() * elapsed_ms / 1000.0
        )
        pixels = int(self._scroll_remainder)
        if pixels <= 0:
            return
        self._scroll_remainder -= pixels

        scrollbar = self._script_view.verticalScrollBar()
        next_value = min(scrollbar.maximum(), scrollbar.value() + pixels)
        scrollbar.setValue(next_value)
        if next_value >= scrollbar.maximum():
            self.pause()
            self._status_label.setText("End of script")
            self._play_pause_button.setText("Start again")

    def _reset_scroll_position(self) -> None:
        self._scroll_remainder = 0.0
        self._script_view.verticalScrollBar().setValue(0)

    def _scroll_units_per_second(self) -> float:
        """Convert the target reading pace into QPlainTextEdit scroll units."""
        word_count = len(re.findall(r"\S+", self._script))
        if word_count == 0:
            return 0.0

        visual_line_count = 0
        block = self._script_view.document().begin()
        while block.isValid():
            layout = block.layout()
            visual_line_count += max(1, layout.lineCount() if layout else 0)
            block = block.next()

        script_duration_seconds = word_count * 60 / self._reading_speed
        return visual_line_count / script_duration_seconds

    def _show_placeholder(self) -> None:
        self._script_view.setPlainText(
            "Your script will appear here.\n\n"
            "Enable the teleprompter in the settings panel, then paste or type what "
            "you want to say."
        )

    def _update_speed_label(self) -> None:
        self._speed_label.setText(f"{self._reading_speed} wpm")
