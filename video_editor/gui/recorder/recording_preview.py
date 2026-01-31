"""Preview widget for screen recording with draggable crop overlay."""

from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGraphicsView, QGraphicsScene,
    QGraphicsRectItem, QSizePolicy
)
from PySide6.QtGui import QColor, QPen, QBrush, QPainter, QCursor
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtMultimedia import QMediaCaptureSession


class CropOverlayItem(QGraphicsRectItem):
    """Draggable crop region overlay with resize handles."""

    HANDLE_SIZE = 12
    EDGE_THRESHOLD = 15

    def __init__(self, parent=None):
        super().__init__(parent)
        self._aspect_ratio: tuple[int, int] | None = None
        self._resolution: tuple[int, int] | None = None
        self._screen_size: tuple[int, int] | None = None
        self._container_rect = QRectF()
        self._drag_mode = None
        self._drag_start_pos = QPointF()
        self._drag_start_rect = QRectF()

        # Visual style
        self.setPen(QPen(QColor(66, 165, 245), 2))  # Blue border
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setAcceptHoverEvents(True)

    def set_container_rect(self, rect: QRectF):
        """Set the container bounds (video area)."""
        self._container_rect = rect
        self._update_rect()

    def set_screen_size(self, width: int, height: int):
        """Set the actual screen size in pixels (for resolution scaling)."""
        self._screen_size = (width, height)
        self._update_rect()

    def set_aspect_ratio(self, ratio: tuple[int, int] | None):
        """Set the aspect ratio constraint."""
        self._aspect_ratio = ratio
        self._resolution = None  # Clear resolution when setting aspect ratio
        self._update_rect()

    def set_resolution(self, resolution: tuple[int, int] | None):
        """Set the fixed resolution constraint.

        Args:
            resolution: Tuple of (width, height) in pixels, or None to clear
        """
        self._resolution = resolution
        self._aspect_ratio = None  # Clear aspect ratio when setting resolution
        self._update_rect()

    def _update_rect(self):
        """Update crop rect based on resolution or aspect ratio."""
        if not self._container_rect.isValid():
            return

        # Fixed resolution mode
        if self._resolution is not None:
            self._update_rect_from_resolution()
        # Aspect ratio mode
        elif self._aspect_ratio is not None:
            self._update_rect_from_aspect()
        else:
            self.setRect(self._container_rect)

    def _update_rect_from_resolution(self):
        """Update crop rect for fixed resolution."""
        if not self._container_rect.isValid() or self._resolution is None:
            self.setRect(self._container_rect)
            return

        req_w, req_h = self._resolution

        # Calculate scale factor (resolution -> screen -> preview)
        if self._screen_size:
            screen_w, screen_h = self._screen_size
            # Scale down if resolution exceeds screen
            screen_scale = min(1.0, screen_w / req_w, screen_h / req_h)
            actual_w = req_w * screen_scale
            actual_h = req_h * screen_scale
        else:
            actual_w, actual_h = req_w, req_h

        # Scale to preview coordinates
        container_w = self._container_rect.width()
        container_h = self._container_rect.height()

        if self._screen_size:
            screen_w, screen_h = self._screen_size
            preview_scale = min(container_w / screen_w, container_h / screen_h)
        else:
            preview_scale = 1.0

        crop_width = actual_w * preview_scale
        crop_height = actual_h * preview_scale

        # Center the crop rect
        x = self._container_rect.x() + (container_w - crop_width) / 2
        y = self._container_rect.y() + (container_h - crop_height) / 2

        self.setRect(QRectF(x, y, crop_width, crop_height))

    def _update_rect_from_aspect(self):
        """Update crop rect to match aspect ratio within container."""
        if not self._container_rect.isValid() or self._aspect_ratio is None:
            self.setRect(self._container_rect)
            return

        target_w, target_h = self._aspect_ratio
        target_ratio = target_w / target_h
        container_ratio = self._container_rect.width() / self._container_rect.height()

        if target_ratio > container_ratio:
            # Target is wider - use full width, reduce height
            crop_width = self._container_rect.width()
            crop_height = crop_width / target_ratio
        else:
            # Target is taller - use full height, reduce width
            crop_height = self._container_rect.height()
            crop_width = crop_height * target_ratio

        # Center the crop rect
        x = self._container_rect.x() + (self._container_rect.width() - crop_width) / 2
        y = self._container_rect.y() + (self._container_rect.height() - crop_height) / 2

        self.setRect(QRectF(x, y, crop_width, crop_height))

    def _get_screen_crop_size(self) -> tuple[int, int]:
        """Get the crop size in actual screen pixels (for FFmpeg)."""
        if not self._screen_size:
            return (0, 0)

        screen_w, screen_h = self._screen_size

        if self._resolution is not None:
            req_w, req_h = self._resolution
            # Scale down if larger than screen
            scale = min(1.0, screen_w / req_w, screen_h / req_h)
            return (int(req_w * scale), int(req_h * scale))
        elif self._aspect_ratio is not None:
            target_w, target_h = self._aspect_ratio
            target_ratio = target_w / target_h
            screen_ratio = screen_w / screen_h
            if target_ratio > screen_ratio:
                return (screen_w, int(screen_w / target_ratio))
            else:
                return (int(screen_h * target_ratio), screen_h)
        else:
            return (screen_w, screen_h)

    def get_normalized_offset(self) -> tuple[float, float]:
        """Get the crop position as normalized offset (0.0-1.0) in screen coordinates.

        The offset is normalized relative to the actual screen size and crop size,
        so FFmpeg can correctly apply it to the screen capture.
        """
        if not self._container_rect.isValid() or not self._screen_size:
            return (0.5, 0.5)

        screen_w, screen_h = self._screen_size
        crop_w, crop_h = self._get_screen_crop_size()

        if crop_w == 0 or crop_h == 0:
            return (0.5, 0.5)

        # Get visual position in container coordinates
        rect = self.rect()
        container_x = rect.x() - self._container_rect.x()
        container_y = rect.y() - self._container_rect.y()

        # Convert container position to screen position
        # container_rect maps to screen_size
        scale_x = screen_w / self._container_rect.width()
        scale_y = screen_h / self._container_rect.height()
        screen_x = container_x * scale_x
        screen_y = container_y * scale_y

        # Normalize relative to screen max movement range
        max_x = screen_w - crop_w
        max_y = screen_h - crop_h

        if max_x <= 0:
            offset_x = 0.5
        else:
            offset_x = screen_x / max_x

        if max_y <= 0:
            offset_y = 0.5
        else:
            offset_y = screen_y / max_y

        # Clamp to valid range
        offset_x = max(0.0, min(1.0, offset_x))
        offset_y = max(0.0, min(1.0, offset_y))

        return (offset_x, offset_y)

    def set_normalized_offset(self, x: float, y: float):
        """Set the crop position from normalized offset in screen coordinates.

        Converts screen-based normalized offset to container coordinates for display.
        """
        if not self._container_rect.isValid() or not self._screen_size:
            return

        screen_w, screen_h = self._screen_size
        crop_w, crop_h = self._get_screen_crop_size()

        if crop_w == 0 or crop_h == 0:
            return

        # Calculate screen position from normalized offset
        max_x = screen_w - crop_w
        max_y = screen_h - crop_h
        screen_x = max_x * x if max_x > 0 else 0
        screen_y = max_y * y if max_y > 0 else 0

        # Convert screen position to container position
        scale_x = self._container_rect.width() / screen_w
        scale_y = self._container_rect.height() / screen_h
        container_x = screen_x * scale_x
        container_y = screen_y * scale_y

        rect = self.rect()
        new_x = self._container_rect.x() + container_x
        new_y = self._container_rect.y() + container_y

        self.setRect(QRectF(new_x, new_y, rect.width(), rect.height()))


class DarkOverlayItem(QGraphicsRectItem):
    """Semi-transparent overlay outside the crop region."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBrush(QBrush(QColor(0, 0, 0, 128)))
        self.setPen(Qt.PenStyle.NoPen)
        self._crop_rect = QRectF()

    def set_crop_rect(self, crop_rect: QRectF):
        """Set the crop region to exclude from overlay."""
        self._crop_rect = crop_rect
        self.update()

    def paint(self, painter, option, widget=None):
        """Paint the overlay with crop region cut out."""
        painter.setBrush(self.brush())
        painter.setPen(self.pen())

        # Draw the full rect
        full_rect = self.rect()
        if self._crop_rect.isValid():
            # Create a path with hole for crop region
            from PySide6.QtGui import QPainterPath
            path = QPainterPath()
            path.addRect(full_rect)
            path.addRect(self._crop_rect)
            painter.drawPath(path)
        else:
            painter.drawRect(full_rect)


class RecordingPreviewView(QGraphicsView):
    """Graphics view for recording preview with crop interaction."""

    crop_changed = Signal(float, float)  # normalized x, y offset

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor(30, 30, 30)))

        self._dragging = False
        self._drag_start = QPointF()
        self._drag_rect_start = QRectF()
        self._crop_overlay: CropOverlayItem | None = None

    def set_crop_overlay(self, overlay: CropOverlayItem):
        """Set the crop overlay item for interaction."""
        self._crop_overlay = overlay

    def mousePressEvent(self, event):
        """Handle mouse press for dragging crop region."""
        if event.button() == Qt.MouseButton.LeftButton and self._crop_overlay:
            pos = self.mapToScene(event.pos())
            if self._crop_overlay.rect().contains(pos):
                self._dragging = True
                self._drag_start = pos
                self._drag_rect_start = self._crop_overlay.rect()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle mouse move for dragging."""
        if self._dragging and self._crop_overlay:
            pos = self.mapToScene(event.pos())
            delta = pos - self._drag_start

            new_rect = self._drag_rect_start.translated(delta)

            # Constrain to container bounds
            container = self._crop_overlay._container_rect
            if new_rect.left() < container.left():
                new_rect.moveLeft(container.left())
            if new_rect.right() > container.right():
                new_rect.moveRight(container.right())
            if new_rect.top() < container.top():
                new_rect.moveTop(container.top())
            if new_rect.bottom() > container.bottom():
                new_rect.moveBottom(container.bottom())

            self._crop_overlay.setRect(new_rect)

            # Update dark overlay
            scene = self.scene()
            for item in scene.items():
                if isinstance(item, DarkOverlayItem):
                    item.set_crop_rect(new_rect)

            event.accept()
            return

        # Update cursor when hovering over crop region
        if self._crop_overlay:
            pos = self.mapToScene(event.pos())
            if self._crop_overlay.rect().contains(pos):
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Handle mouse release."""
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)

            # Emit the new offset
            if self._crop_overlay:
                x, y = self._crop_overlay.get_normalized_offset()
                self.crop_changed.emit(x, y)

            event.accept()
            return

        super().mouseReleaseEvent(event)

    def resizeEvent(self, event):
        """Handle resize to fit content."""
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


class RecordingPreview(QWidget):
    """Widget showing live screen capture preview with crop overlay.

    Displays a preview of the screen being captured. When a crop mode
    (resolution or aspect ratio) is selected, shows a draggable overlay
    indicating the crop region.

    Signals:
        crop_offset_changed: Emitted when crop region is moved (x, y)
    """

    crop_offset_changed = Signal(float, float)  # normalized x, y

    def __init__(self, parent=None):
        super().__init__(parent)

        self._aspect_ratio: tuple[int, int] | None = None
        self._resolution: tuple[int, int] | None = None
        self._screen_size: tuple[int, int] | None = None
        self._show_overlay = False

        self._setup_ui()

    def _setup_ui(self):
        """Set up the preview UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Graphics scene and view
        self._scene = QGraphicsScene()
        self._view = RecordingPreviewView()
        self._view.setScene(self._scene)
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._view)

        # Video item for preview
        self._video_item = QGraphicsVideoItem()
        self._scene.addItem(self._video_item)

        # Dark overlay (hidden by default)
        self._dark_overlay = DarkOverlayItem()
        self._dark_overlay.setVisible(False)
        self._scene.addItem(self._dark_overlay)

        # Crop overlay (hidden by default)
        self._crop_overlay = CropOverlayItem()
        self._crop_overlay.setVisible(False)
        self._scene.addItem(self._crop_overlay)

        self._view.set_crop_overlay(self._crop_overlay)
        self._view.crop_changed.connect(self._on_crop_changed)

        # Connect video item size changes
        self._video_item.nativeSizeChanged.connect(self._on_video_size_changed)

    def _on_video_size_changed(self, size):
        """Handle video native size changes."""
        if size.isValid():
            rect = QRectF(0, 0, size.width(), size.height())
            self._video_item.setSize(size)
            self._scene.setSceneRect(rect)
            self._dark_overlay.setRect(rect)
            self._crop_overlay.set_container_rect(rect)
            self._view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
            self._update_overlays()

    def _on_crop_changed(self, x: float, y: float):
        """Handle crop region being moved."""
        self.crop_offset_changed.emit(x, y)

    def _update_overlays(self):
        """Update overlay visibility and position."""
        if self._show_overlay:
            # Apply screen size if available
            if self._screen_size:
                self._crop_overlay.set_screen_size(*self._screen_size)

            # Resolution mode takes precedence
            if self._resolution is not None:
                self._crop_overlay.set_resolution(self._resolution)
            elif self._aspect_ratio is not None:
                self._crop_overlay.set_aspect_ratio(self._aspect_ratio)

            self._crop_overlay.setVisible(True)
            self._dark_overlay.set_crop_rect(self._crop_overlay.rect())
            self._dark_overlay.setVisible(True)
        else:
            self._crop_overlay.setVisible(False)
            self._dark_overlay.setVisible(False)

    def set_capture_session(self, session: QMediaCaptureSession):
        """Connect the capture session for preview display."""
        session.setVideoOutput(self._video_item)

    def set_screen_size(self, width: int, height: int):
        """Set the actual screen size in pixels (for resolution scaling)."""
        self._screen_size = (width, height)
        self._crop_overlay.set_screen_size(width, height)
        self._update_overlays()

    def set_aspect_ratio(self, ratio: tuple[int, int] | None):
        """Set the aspect ratio for the crop overlay.

        Args:
            ratio: Tuple of (width, height) for aspect ratio, or None to hide overlay
        """
        self._aspect_ratio = ratio
        self._resolution = None  # Clear resolution
        self._show_overlay = ratio is not None
        self._update_overlays()

    def set_resolution(self, resolution: tuple[int, int] | None):
        """Set the fixed resolution for the crop overlay.

        Args:
            resolution: Tuple of (width, height) in pixels, or None to hide overlay
        """
        self._resolution = resolution
        self._aspect_ratio = None  # Clear aspect ratio
        self._show_overlay = resolution is not None
        self._update_overlays()

    def set_crop_mode(self, resolution: tuple[int, int] | None, aspect_ratio: tuple[int, int] | None):
        """Set the crop mode (resolution or aspect ratio).

        Args:
            resolution: Fixed resolution (takes precedence), or None
            aspect_ratio: Aspect ratio, or None
        """
        self._resolution = resolution
        self._aspect_ratio = aspect_ratio
        self._show_overlay = resolution is not None or aspect_ratio is not None
        self._update_overlays()

    def set_crop_offset(self, x: float, y: float):
        """Set the crop region position."""
        self._crop_overlay.set_normalized_offset(x, y)
        if self._show_overlay:
            self._dark_overlay.set_crop_rect(self._crop_overlay.rect())

    def get_crop_offset(self) -> tuple[float, float]:
        """Get the current crop region position."""
        return self._crop_overlay.get_normalized_offset()
