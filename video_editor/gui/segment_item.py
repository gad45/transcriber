"""Timeline segment graphics items."""

from PySide6.QtCore import Qt, QRectF, Signal, QObject
from PySide6.QtWidgets import (
    QGraphicsRectItem, QGraphicsItem, QGraphicsSceneMouseEvent,
    QMenu, QGraphicsSimpleTextItem
)
from PySide6.QtGui import QBrush, QPen, QColor, QFont


class SegmentSignals(QObject):
    """Signals for segment items (QGraphicsItems can't have signals directly)."""
    clicked = Signal(int)  # segment index
    double_clicked = Signal(int)  # segment index
    toggle_keep = Signal(int)  # segment index
    context_menu = Signal(int, object)  # segment index, QPoint


class SegmentItem(QGraphicsRectItem):
    """
    A visual representation of a video segment on the timeline.

    Colors:
    - Green: Kept segment
    - Red: Cut segment (retake or marked for removal)
    - Yellow border: Retake candidate
    - Gray: Silence/gap
    """

    # Class-level colors
    COLOR_KEEP = QColor(76, 175, 80)       # Green
    COLOR_CUT = QColor(244, 67, 54)        # Red
    COLOR_SILENCE = QColor(158, 158, 158)  # Gray
    COLOR_RETAKE = QColor(255, 193, 7)     # Yellow (border)
    COLOR_HOVER = QColor(255, 255, 255, 50)  # White overlay

    def __init__(
        self,
        segment_index: int,
        start_time: float,
        end_time: float,
        is_kept: bool,
        is_retake_candidate: bool = False,
        text_preview: str = "",
        pixels_per_second: float = 50.0,
        height: float = 40.0,
        signals: SegmentSignals = None
    ):
        super().__init__()

        self.segment_index = segment_index
        self.start_time = start_time
        self.end_time = end_time
        self.is_kept = is_kept
        self.is_retake_candidate = is_retake_candidate
        self.text_preview = text_preview
        self.pixels_per_second = pixels_per_second
        self.signals = signals or SegmentSignals()

        # Calculate position and size
        x = start_time * pixels_per_second
        width = (end_time - start_time) * pixels_per_second
        self.setRect(0, 0, max(width, 2), height)  # Min 2px width
        self.setPos(x, 0)

        # Enable interactions
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

        # Set up appearance
        self._update_appearance()

        # Add text label if there's room
        if width > 50 and text_preview:
            self._add_text_label(width, height)

    def _update_appearance(self):
        """Update colors based on segment state."""
        if self.is_kept:
            color = self.COLOR_KEEP
        else:
            color = self.COLOR_CUT

        self.setBrush(QBrush(color))

        # Border for retake candidates
        if self.is_retake_candidate:
            pen = QPen(self.COLOR_RETAKE, 2)
        else:
            pen = QPen(color.darker(120), 1)

        self.setPen(pen)

    def _add_text_label(self, width: float, height: float):
        """Add a text label inside the segment."""
        # Truncate text to fit
        max_chars = int(width / 7)  # Rough estimate of characters that fit
        text = self.text_preview[:max_chars]
        if len(self.text_preview) > max_chars:
            text = text[:-3] + "..."

        label = QGraphicsSimpleTextItem(text, self)
        label.setBrush(QBrush(Qt.GlobalColor.white))
        font = QFont("Arial", 9)
        label.setFont(font)

        # Center the text
        text_rect = label.boundingRect()
        label.setPos(
            (width - text_rect.width()) / 2,
            (height - text_rect.height()) / 2
        )

    def set_kept(self, kept: bool):
        """Update the kept state and refresh appearance."""
        self.is_kept = kept
        self._update_appearance()

    def update_scale(self, pixels_per_second: float):
        """Update the segment's position and size when scale changes."""
        self.pixels_per_second = pixels_per_second
        x = self.start_time * pixels_per_second
        width = (self.end_time - self.start_time) * pixels_per_second
        self.setRect(0, 0, max(width, 2), self.rect().height())
        self.setPos(x, self.pos().y())

    # Event handlers

    def hoverEnterEvent(self, event):
        """Highlight on hover."""
        self.setOpacity(0.85)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        """Remove highlight."""
        self.setOpacity(1.0)
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        """Handle mouse click."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.signals.clicked.emit(self.segment_index)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent):
        """Handle double-click to toggle keep/cut."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.signals.double_clicked.emit(self.segment_index)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        """Handle right-click for context menu."""
        self.signals.context_menu.emit(self.segment_index, event.screenPos())


class PlayheadItem(QGraphicsRectItem):
    """
    The playhead indicator that shows current playback position.
    """

    def __init__(self, height: float = 60.0):
        super().__init__(0, 0, 2, height)
        self.setBrush(QBrush(QColor(255, 255, 255)))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setZValue(100)  # Always on top

    def set_position(self, time_seconds: float, pixels_per_second: float):
        """Update playhead position."""
        x = time_seconds * pixels_per_second
        self.setPos(x - 1, 0)  # -1 to center the 2px width
