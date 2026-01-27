"""Timeline widget for visualizing and editing video segments."""

from PySide6.QtCore import Qt, Signal, Slot, QRectF, QPointF
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QWidget, QVBoxLayout,
    QHBoxLayout, QSlider, QLabel, QMenu
)
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QWheelEvent

from .segment_item import SegmentItem, PlayheadItem, SegmentSignals
from .models import EditSession


class TimeRuler(QWidget):
    """Time ruler widget showing timestamps above the timeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(25)
        self.duration_seconds = 0.0
        self.pixels_per_second = 50.0
        self.scroll_offset = 0

    def set_duration(self, seconds: float):
        self.duration_seconds = seconds
        self.update()

    def set_scale(self, pixels_per_second: float):
        self.pixels_per_second = pixels_per_second
        self.update()

    def set_scroll_offset(self, offset: int):
        self.scroll_offset = offset
        self.update()

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        painter.fillRect(self.rect(), QColor(40, 40, 40))

        # Draw tick marks and labels
        painter.setPen(QPen(QColor(200, 200, 200)))
        font = QFont("Arial", 9)
        painter.setFont(font)

        # Determine tick interval based on scale
        if self.pixels_per_second >= 100:
            tick_interval = 1.0  # Every second
        elif self.pixels_per_second >= 50:
            tick_interval = 2.0  # Every 2 seconds
        elif self.pixels_per_second >= 20:
            tick_interval = 5.0  # Every 5 seconds
        elif self.pixels_per_second >= 10:
            tick_interval = 10.0  # Every 10 seconds
        else:
            tick_interval = 30.0  # Every 30 seconds

        # Start from visible area
        start_time = max(0, self.scroll_offset / self.pixels_per_second)
        end_time = min(self.duration_seconds, start_time + self.width() / self.pixels_per_second + tick_interval)

        # Align to tick interval
        current_time = (int(start_time / tick_interval)) * tick_interval

        while current_time <= end_time:
            x = int(current_time * self.pixels_per_second - self.scroll_offset)

            if 0 <= x <= self.width():
                # Draw tick
                painter.drawLine(x, 15, x, 25)

                # Draw label
                minutes = int(current_time) // 60
                seconds = int(current_time) % 60
                label = f"{minutes}:{seconds:02d}"
                painter.drawText(x - 15, 12, label)

            current_time += tick_interval

        painter.end()


class TimelineView(QGraphicsView):
    """
    Graphics view for the segment timeline.

    Displays segments as colored blocks:
    - Green: Kept segments
    - Red: Cut segments
    - Yellow border: Retake candidates
    """

    segment_clicked = Signal(int)  # segment index
    segment_double_clicked = Signal(int)
    seek_requested = Signal(float)  # time in seconds
    toggle_segment = Signal(int)  # segment index

    def __init__(self, parent=None):
        super().__init__(parent)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # Settings
        self.pixels_per_second = 50.0
        self.segment_height = 40.0
        self.duration_seconds = 0.0

        # Items
        self._segment_items: list[SegmentItem] = []
        self._playhead = PlayheadItem(self.segment_height + 10)
        self._scene.addItem(self._playhead)

        # Shared signals for all segments
        self._segment_signals = SegmentSignals()
        self._segment_signals.clicked.connect(self._on_segment_clicked)
        self._segment_signals.double_clicked.connect(self._on_segment_double_clicked)
        self._segment_signals.context_menu.connect(self._on_segment_context_menu)

        # Configure view
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QBrush(QColor(30, 30, 30)))
        self.setMinimumHeight(60)
        self.setMaximumHeight(80)

        # For click-to-seek
        self.setMouseTracking(True)

    def load_session(self, session: EditSession):
        """Load segments from an edit session."""
        self.clear_segments()
        self.duration_seconds = session.video_duration

        # Update scene rect
        width = self.duration_seconds * self.pixels_per_second
        self._scene.setSceneRect(0, 0, width, self.segment_height + 10)

        # Add segment items
        for i, seg in enumerate(session.original_segments):
            is_kept = session.is_segment_kept(i)
            is_retake = False
            if i < len(session.analyzed_segments):
                is_retake = session.analyzed_segments[i].retake_group_id is not None

            text_preview = seg.text[:30] if seg.text else ""

            item = SegmentItem(
                segment_index=i,
                start_time=seg.start,
                end_time=seg.end,
                is_kept=is_kept,
                is_retake_candidate=is_retake,
                text_preview=text_preview,
                pixels_per_second=self.pixels_per_second,
                height=self.segment_height,
                signals=self._segment_signals
            )
            self._scene.addItem(item)
            self._segment_items.append(item)

        # Ensure playhead is on top
        self._playhead.setZValue(100)

    def clear_segments(self):
        """Remove all segment items."""
        for item in self._segment_items:
            self._scene.removeItem(item)
        self._segment_items.clear()

    def update_segment(self, index: int, is_kept: bool):
        """Update a single segment's appearance."""
        if 0 <= index < len(self._segment_items):
            self._segment_items[index].set_kept(is_kept)

    def set_playhead_position(self, time_seconds: float):
        """Update the playhead position."""
        self._playhead.set_position(time_seconds, self.pixels_per_second)

        # Auto-scroll to keep playhead visible
        playhead_x = time_seconds * self.pixels_per_second
        visible_rect = self.mapToScene(self.viewport().rect()).boundingRect()

        if playhead_x < visible_rect.left() + 50 or playhead_x > visible_rect.right() - 50:
            self.centerOn(playhead_x, self.segment_height / 2)

    def set_scale(self, pixels_per_second: float):
        """Update the timeline scale (zoom level)."""
        self.pixels_per_second = pixels_per_second

        # Update scene size
        width = self.duration_seconds * self.pixels_per_second
        self._scene.setSceneRect(0, 0, width, self.segment_height + 10)

        # Update all segment items
        for item in self._segment_items:
            item.update_scale(pixels_per_second)

    def get_scroll_offset(self) -> int:
        """Get current horizontal scroll offset."""
        return self.horizontalScrollBar().value()

    # Event handlers

    def mousePressEvent(self, event):
        """Handle click-to-seek."""
        if event.button() == Qt.MouseButton.LeftButton:
            # Check if clicking on empty space (not a segment)
            item = self.itemAt(event.pos())
            if item is None or item == self._playhead:
                scene_pos = self.mapToScene(event.pos())
                time_seconds = scene_pos.x() / self.pixels_per_second
                time_seconds = max(0, min(time_seconds, self.duration_seconds))
                self.seek_requested.emit(time_seconds)
                return

        super().mousePressEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        """Handle scroll wheel for zooming."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Zoom with Ctrl+scroll
            delta = event.angleDelta().y()
            factor = 1.1 if delta > 0 else 0.9
            new_scale = max(5, min(200, self.pixels_per_second * factor))
            self.set_scale(new_scale)
            event.accept()
        else:
            super().wheelEvent(event)

    # Private slots

    @Slot(int)
    def _on_segment_clicked(self, index: int):
        self.segment_clicked.emit(index)

    @Slot(int)
    def _on_segment_double_clicked(self, index: int):
        self.segment_double_clicked.emit(index)
        self.toggle_segment.emit(index)

    @Slot(int, object)
    def _on_segment_context_menu(self, index: int, pos):
        """Show context menu for a segment."""
        if 0 <= index < len(self._segment_items):
            item = self._segment_items[index]

            menu = QMenu()
            if item.is_kept:
                action = menu.addAction("Remove segment (cut)")
            else:
                action = menu.addAction("Re-enable segment (keep)")

            result = menu.exec(pos)
            if result == action:
                self.toggle_segment.emit(index)


class Timeline(QWidget):
    """
    Complete timeline widget with ruler and zoom controls.
    """

    segment_clicked = Signal(int)
    segment_double_clicked = Signal(int)
    seek_requested = Signal(float)
    toggle_segment = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Time ruler
        self._ruler = TimeRuler()
        layout.addWidget(self._ruler)

        # Timeline view
        self._view = TimelineView()
        layout.addWidget(self._view)

        # Zoom controls
        zoom_layout = QHBoxLayout()
        zoom_layout.setContentsMargins(4, 4, 4, 4)

        zoom_layout.addWidget(QLabel("Zoom:"))

        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(5, 200)
        self._zoom_slider.setValue(50)
        self._zoom_slider.setFixedWidth(150)
        zoom_layout.addWidget(self._zoom_slider)

        self._zoom_label = QLabel("50 px/s")
        self._zoom_label.setFixedWidth(60)
        zoom_layout.addWidget(self._zoom_label)

        zoom_layout.addStretch()

        layout.addLayout(zoom_layout)

    def _connect_signals(self):
        # Forward signals from view
        self._view.segment_clicked.connect(self.segment_clicked)
        self._view.segment_double_clicked.connect(self.segment_double_clicked)
        self._view.seek_requested.connect(self.seek_requested)
        self._view.toggle_segment.connect(self.toggle_segment)

        # Zoom slider
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)

        # Scroll synchronization
        self._view.horizontalScrollBar().valueChanged.connect(
            lambda v: self._ruler.set_scroll_offset(v)
        )

    def load_session(self, session: EditSession):
        """Load an edit session."""
        self._view.load_session(session)
        self._ruler.set_duration(session.video_duration)
        self._ruler.set_scale(self._view.pixels_per_second)

    def update_segment(self, index: int, is_kept: bool):
        """Update a segment's appearance."""
        self._view.update_segment(index, is_kept)

    def set_playhead_position(self, time_seconds: float):
        """Update playhead position."""
        self._view.set_playhead_position(time_seconds)

    @Slot(int)
    def _on_zoom_changed(self, value: int):
        self._view.set_scale(float(value))
        self._ruler.set_scale(float(value))
        self._zoom_label.setText(f"{value} px/s")
