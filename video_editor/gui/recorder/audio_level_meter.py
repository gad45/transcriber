"""Audio level meter widget for visual audio input feedback."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QPen


class AudioLevelMeter(QWidget):
    """Visual audio level meter showing input volume.

    Displays a horizontal bar that fills based on audio input level.
    Uses a green-yellow-red gradient to indicate level.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self._level = 0.0  # Current level (0.0-1.0)
        self._peak_level = 0.0  # Peak hold level
        self._peak_hold_time = 0  # Frames to hold peak

        self.setMinimumHeight(20)
        self.setMaximumHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Peak decay timer
        self._decay_timer = QTimer(self)
        self._decay_timer.timeout.connect(self._decay_peak)
        self._decay_timer.start(50)  # 20 fps

    def set_level(self, level: float):
        """Set the current audio level (0.0-1.0)."""
        self._level = max(0.0, min(1.0, level))

        # Update peak
        if self._level > self._peak_level:
            self._peak_level = self._level
            self._peak_hold_time = 20  # Hold for ~1 second

        self.update()

    def _decay_peak(self):
        """Decay the peak level over time."""
        if self._peak_hold_time > 0:
            self._peak_hold_time -= 1
        else:
            self._peak_level = max(self._level, self._peak_level * 0.95)
            if self._peak_level < 0.01:
                self._peak_level = 0.0

    def paintEvent(self, event):
        """Paint the level meter."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        bg_color = QColor(40, 40, 40)
        painter.fillRect(self.rect(), bg_color)

        # Border
        border_color = QColor(60, 60, 60)
        painter.setPen(QPen(border_color, 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        # Calculate meter dimensions
        margin = 3
        meter_rect = self.rect().adjusted(margin, margin, -margin, -margin)
        meter_width = meter_rect.width()
        meter_height = meter_rect.height()

        # Level bar gradient (green -> yellow -> red)
        gradient = QLinearGradient(meter_rect.left(), 0, meter_rect.right(), 0)
        gradient.setColorAt(0.0, QColor(76, 175, 80))   # Green
        gradient.setColorAt(0.6, QColor(76, 175, 80))   # Green
        gradient.setColorAt(0.75, QColor(255, 193, 7))  # Yellow
        gradient.setColorAt(0.9, QColor(255, 152, 0))   # Orange
        gradient.setColorAt(1.0, QColor(244, 67, 54))   # Red

        # Draw level bar
        level_width = int(meter_width * self._level)
        if level_width > 0:
            level_rect = meter_rect.adjusted(0, 0, -(meter_width - level_width), 0)
            painter.fillRect(level_rect, gradient)

        # Draw peak indicator
        if self._peak_level > 0.01:
            peak_x = meter_rect.left() + int(meter_width * self._peak_level)
            peak_color = QColor(255, 255, 255)
            painter.setPen(QPen(peak_color, 2))
            painter.drawLine(peak_x, meter_rect.top(), peak_x, meter_rect.bottom())

        # Draw scale marks
        painter.setPen(QPen(QColor(80, 80, 80), 1))
        for i in range(1, 10):
            x = meter_rect.left() + int(meter_width * i / 10)
            painter.drawLine(x, meter_rect.top(), x, meter_rect.top() + 3)
            painter.drawLine(x, meter_rect.bottom() - 3, x, meter_rect.bottom())

        painter.end()

    def reset(self):
        """Reset the level and peak."""
        self._level = 0.0
        self._peak_level = 0.0
        self._peak_hold_time = 0
        self.update()
