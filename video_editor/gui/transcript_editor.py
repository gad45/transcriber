"""Transcript editor widget for viewing and editing segment text."""

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel,
    QLineEdit, QCheckBox, QFrame, QPushButton, QSizePolicy
)
from PySide6.QtGui import QColor, QPalette

from .models import EditSession


def format_time(seconds: float) -> str:
    """Format seconds as MM:SS.ms."""
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    ms = int((seconds % 1) * 100)
    return f"{minutes:02d}:{secs:02d}.{ms:02d}"


class SegmentWidget(QFrame):
    """Widget for displaying and editing a single segment."""

    text_changed = Signal(int, str)  # index, new_text
    keep_changed = Signal(int, bool)  # index, is_kept
    clicked = Signal(int)  # index

    # Colors
    COLOR_KEEP = QColor(76, 175, 80, 40)  # Light green
    COLOR_CUT = QColor(244, 67, 54, 40)   # Light red
    COLOR_SELECTED = QColor(33, 150, 243, 60)  # Blue

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        self.is_selected = False

        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Plain)
        self.setLineWidth(1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Header row: time + keep checkbox
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        self._time_label = QLabel()
        self._time_label.setStyleSheet("color: #888; font-size: 11px;")
        header_layout.addWidget(self._time_label)

        header_layout.addStretch()

        self._reason_label = QLabel()
        self._reason_label.setStyleSheet("color: #f44336; font-size: 10px;")
        header_layout.addWidget(self._reason_label)

        self._keep_checkbox = QCheckBox("Keep")
        self._keep_checkbox.stateChanged.connect(self._on_keep_changed)
        header_layout.addWidget(self._keep_checkbox)

        layout.addLayout(header_layout)

        # Text editor
        self._text_edit = QLineEdit()
        self._text_edit.setStyleSheet("""
            QLineEdit {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border-color: #2196f3;
            }
        """)
        self._text_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._text_edit)

    def set_data(
        self,
        start_time: float,
        end_time: float,
        text: str,
        is_kept: bool,
        reason: str = ""
    ):
        """Set the segment data."""
        self._time_label.setText(f"{format_time(start_time)} - {format_time(end_time)}")
        self._text_edit.setText(text)

        # Block signals while setting checkbox
        self._keep_checkbox.blockSignals(True)
        self._keep_checkbox.setChecked(is_kept)
        self._keep_checkbox.blockSignals(False)

        self._reason_label.setText(reason if not is_kept else "")

        self._update_background(is_kept)

    def _update_background(self, is_kept: bool):
        """Update background color based on keep state."""
        if self.is_selected:
            color = self.COLOR_SELECTED
        elif is_kept:
            color = self.COLOR_KEEP
        else:
            color = self.COLOR_CUT

        self.setStyleSheet(f"""
            SegmentWidget {{
                background-color: rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()});
                border-radius: 4px;
            }}
        """)

    def set_selected(self, selected: bool):
        """Set the selection state."""
        self.is_selected = selected
        self._update_background(self._keep_checkbox.isChecked())

    def mousePressEvent(self, event):
        """Handle click to select."""
        self.clicked.emit(self.index)
        super().mousePressEvent(event)

    @Slot()
    def _on_text_changed(self):
        self.text_changed.emit(self.index, self._text_edit.text())

    @Slot(int)
    def _on_keep_changed(self, state: int):
        is_kept = state == Qt.CheckState.Checked.value
        self._update_background(is_kept)
        self.keep_changed.emit(self.index, is_kept)


class TranscriptEditor(QWidget):
    """
    Scrollable list of segment editors.

    Allows viewing and editing segment text, and toggling keep/cut status.
    """

    text_changed = Signal(int, str)  # segment index, new text
    keep_changed = Signal(int, bool)  # segment index, is_kept
    segment_clicked = Signal(int)  # segment index

    def __init__(self, parent=None):
        super().__init__(parent)

        self._session: EditSession | None = None
        self._segment_widgets: list[SegmentWidget] = []
        self._selected_index: int = -1

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setStyleSheet("background-color: #2d2d2d; padding: 8px;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel("Transcript")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        self._stats_label = QLabel()
        self._stats_label.setStyleSheet("color: #888;")
        header_layout.addWidget(self._stats_label)

        layout.addWidget(header)

        # Scroll area for segments
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: #1e1e1e;
            }
        """)

        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(8, 8, 8, 8)
        self._container_layout.setSpacing(6)
        self._container_layout.addStretch()  # Push items to top

        self._scroll_area.setWidget(self._container)
        layout.addWidget(self._scroll_area, stretch=1)

    def load_session(self, session: EditSession):
        """Load segments from an edit session."""
        self._session = session
        self._clear_widgets()

        # Create widget for each segment
        for i, seg in enumerate(session.original_segments):
            widget = SegmentWidget(i)
            widget.text_changed.connect(self._on_text_changed)
            widget.keep_changed.connect(self._on_keep_changed)
            widget.clicked.connect(self._on_segment_clicked)

            text = session.get_segment_text(i)
            is_kept = session.is_segment_kept(i)
            reason = session.get_segment_reason(i)

            widget.set_data(seg.start, seg.end, text, is_kept, reason)

            # Insert before the stretch
            self._container_layout.insertWidget(
                self._container_layout.count() - 1,
                widget
            )
            self._segment_widgets.append(widget)

        self._update_stats()

    def _clear_widgets(self):
        """Remove all segment widgets."""
        for widget in self._segment_widgets:
            self._container_layout.removeWidget(widget)
            widget.deleteLater()
        self._segment_widgets.clear()
        self._selected_index = -1

    def update_segment(self, index: int, is_kept: bool):
        """Update a segment's keep state."""
        if 0 <= index < len(self._segment_widgets) and self._session:
            widget = self._segment_widgets[index]
            seg = self._session.original_segments[index]
            text = self._session.get_segment_text(index)
            reason = self._session.get_segment_reason(index)
            widget.set_data(seg.start, seg.end, text, is_kept, reason)
            self._update_stats()

    def select_segment(self, index: int):
        """Select a segment and scroll to it."""
        if self._selected_index >= 0 and self._selected_index < len(self._segment_widgets):
            self._segment_widgets[self._selected_index].set_selected(False)

        if 0 <= index < len(self._segment_widgets):
            self._selected_index = index
            widget = self._segment_widgets[index]
            widget.set_selected(True)

            # Scroll to make visible
            self._scroll_area.ensureWidgetVisible(widget, 50, 50)

    def highlight_current_time(self, time_seconds: float):
        """Highlight the segment at the current playback time."""
        if not self._session:
            return

        # Find the segment containing this time
        for i, seg in enumerate(self._session.original_segments):
            if seg.start <= time_seconds < seg.end:
                if i != self._selected_index:
                    self.select_segment(i)
                break

    def _update_stats(self):
        """Update the statistics label."""
        if not self._session:
            return

        total = len(self._session.original_segments)
        kept = sum(1 for i in range(total) if self._session.is_segment_kept(i))
        self._stats_label.setText(f"{kept}/{total} kept")

    @Slot(int, str)
    def _on_text_changed(self, index: int, text: str):
        if self._session:
            self._session.set_segment_text(index, text)
        self.text_changed.emit(index, text)

    @Slot(int, bool)
    def _on_keep_changed(self, index: int, is_kept: bool):
        if self._session:
            self._session.set_segment_kept(index, is_kept)
        self._update_stats()
        self.keep_changed.emit(index, is_kept)

    @Slot(int)
    def _on_segment_clicked(self, index: int):
        self.select_segment(index)
        self.segment_clicked.emit(index)
