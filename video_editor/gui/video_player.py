"""Video player widget with playback controls and crop/pan support."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot, QUrl, QRectF, QSizeF, QPointF
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel,
    QStyle, QSizePolicy, QGraphicsScene, QGraphicsView, QGraphicsRectItem,
    QGraphicsTextItem
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtGui import QBrush, QColor, QPen, QPainter, QCursor, QFont

from .models import CropConfig, CaptionSettings
from ..transcriber import Token


def format_time(ms: int) -> str:
    """Format milliseconds as MM:SS."""
    seconds = ms // 1000
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


class VideoView(QGraphicsView):
    """Custom graphics view for video display with interactive crop and caption adjustment."""

    # Signal emitted when crop rectangle changes during drag
    crop_rect_changed = Signal(float, float, float, float)  # x, y, width, height (in pixels)
    crop_drag_finished = Signal()

    # Signal emitted when caption box changes during drag
    caption_rect_changed = Signal(float, float, float, float)  # x, y, width, height (in pixels)
    caption_drag_finished = Signal()

    # Interaction modes
    MODE_NONE = 0
    MODE_CROP = 1
    MODE_CAPTION = 2

    # Drag modes for different interactions
    DRAG_NONE = 0
    DRAG_NEW = 1        # Creating a new crop selection
    DRAG_MOVE = 2       # Moving the entire region
    DRAG_EDGE_LEFT = 3
    DRAG_EDGE_RIGHT = 4
    DRAG_EDGE_TOP = 5
    DRAG_EDGE_BOTTOM = 6
    DRAG_CORNER_TL = 7  # Top-left
    DRAG_CORNER_TR = 8  # Top-right
    DRAG_CORNER_BL = 9  # Bottom-left
    DRAG_CORNER_BR = 10 # Bottom-right

    # Hit detection threshold (in scene coordinates, will be scaled)
    EDGE_THRESHOLD = 15

    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor(0, 0, 0)))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMouseTracking(True)  # Enable mouse tracking for cursor updates

        # Interaction mode
        self._interaction_mode = self.MODE_NONE

        # Crop interaction state
        self._crop_mode = False
        self._drag_mode = self.DRAG_NONE
        self._drag_start: QPointF | None = None
        self._drag_current: QPointF | None = None
        self._aspect_ratio: tuple[int, int] | None = None  # e.g., (16, 9) or None for free
        self._video_width = 1920
        self._video_height = 1080

        # Current crop rect (for adjustment operations)
        self._current_crop_rect: QRectF | None = None

        # Caption interaction state
        self._caption_mode = False
        self._current_caption_rect: QRectF | None = None

    def set_crop_interaction(self, enabled: bool):
        """Enable or disable crop mouse interaction."""
        self._crop_mode = enabled
        if enabled:
            self._interaction_mode = self.MODE_CROP
            self._caption_mode = False
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        else:
            if not self._caption_mode:
                self._interaction_mode = self.MODE_NONE
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self._drag_mode = self.DRAG_NONE
            self._drag_start = None
            self._drag_current = None

    def set_caption_interaction(self, enabled: bool):
        """Enable or disable caption mouse interaction."""
        self._caption_mode = enabled
        if enabled:
            self._interaction_mode = self.MODE_CAPTION
            self._crop_mode = False
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        else:
            if not self._crop_mode:
                self._interaction_mode = self.MODE_NONE
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self._drag_mode = self.DRAG_NONE
            self._drag_start = None
            self._drag_current = None

    def set_aspect_ratio(self, ratio: tuple[int, int] | None):
        """Set the aspect ratio constraint for crop selection."""
        self._aspect_ratio = ratio

    def set_video_dimensions(self, width: int, height: int):
        """Set video dimensions for coordinate calculations."""
        self._video_width = width
        self._video_height = height

    def set_current_crop_rect(self, rect: QRectF | None):
        """Set the current crop rectangle for adjustment operations."""
        self._current_crop_rect = rect

    def set_current_caption_rect(self, rect: QRectF | None):
        """Set the current caption rectangle for adjustment operations."""
        self._current_caption_rect = rect

    def resizeEvent(self, event):
        """Fit the scene to the view when resized."""
        super().resizeEvent(event)
        if self.scene():
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _get_edge_threshold(self) -> float:
        """Get edge detection threshold scaled for current zoom level."""
        # Scale threshold based on view transform
        transform = self.transform()
        scale = transform.m11()  # Horizontal scale factor
        if scale > 0:
            return self.EDGE_THRESHOLD / scale
        return self.EDGE_THRESHOLD

    def _hit_test_caption(self, pos: QPointF) -> int:
        """Determine what part of the caption rect the position hits."""
        if self._current_caption_rect is None or self._current_caption_rect.isEmpty():
            return self.DRAG_NONE

        rect = self._current_caption_rect
        # Use a smaller threshold for captions (they're smaller than crop regions)
        threshold = min(self._get_edge_threshold(), 10)

        x, y = pos.x(), pos.y()
        left, top = rect.left(), rect.top()
        right, bottom = rect.right(), rect.bottom()

        # First check if inside the rectangle at all
        if not rect.contains(pos):
            # Also check slightly outside for edge detection
            if not (left - threshold < x < right + threshold and
                    top - threshold < y < bottom + threshold):
                return self.DRAG_NONE

        on_left = abs(x - left) < threshold
        on_right = abs(x - right) < threshold
        on_top = abs(y - top) < threshold
        on_bottom = abs(y - bottom) < threshold

        # Check corners first (for resize)
        if on_left and on_top:
            return self.DRAG_CORNER_TL
        if on_right and on_top:
            return self.DRAG_CORNER_TR
        if on_left and on_bottom:
            return self.DRAG_CORNER_BL
        if on_right and on_bottom:
            return self.DRAG_CORNER_BR

        # Check edges (all edges for full box resize)
        if on_left:
            return self.DRAG_EDGE_LEFT
        if on_right:
            return self.DRAG_EDGE_RIGHT
        if on_top:
            return self.DRAG_EDGE_TOP
        if on_bottom:
            return self.DRAG_EDGE_BOTTOM

        # If inside the rectangle but not on an edge, it's a move
        if rect.contains(pos):
            return self.DRAG_MOVE

        return self.DRAG_NONE

    def _hit_test(self, pos: QPointF) -> int:
        """Determine what part of the crop rect the position hits."""
        if self._current_crop_rect is None or self._current_crop_rect.isEmpty():
            return self.DRAG_NEW

        rect = self._current_crop_rect
        threshold = self._get_edge_threshold()

        x, y = pos.x(), pos.y()
        left, top = rect.left(), rect.top()
        right, bottom = rect.right(), rect.bottom()

        on_left = abs(x - left) < threshold
        on_right = abs(x - right) < threshold
        on_top = abs(y - top) < threshold
        on_bottom = abs(y - bottom) < threshold

        in_x = left - threshold < x < right + threshold
        in_y = top - threshold < y < bottom + threshold

        # Check corners first (they take priority)
        if on_left and on_top:
            return self.DRAG_CORNER_TL
        if on_right and on_top:
            return self.DRAG_CORNER_TR
        if on_left and on_bottom:
            return self.DRAG_CORNER_BL
        if on_right and on_bottom:
            return self.DRAG_CORNER_BR

        # Check edges
        if on_left and in_y:
            return self.DRAG_EDGE_LEFT
        if on_right and in_y:
            return self.DRAG_EDGE_RIGHT
        if on_top and in_x:
            return self.DRAG_EDGE_TOP
        if on_bottom and in_x:
            return self.DRAG_EDGE_BOTTOM

        # Check if inside (for move)
        if rect.contains(pos):
            return self.DRAG_MOVE

        # Outside - start new selection
        return self.DRAG_NEW

    def _update_cursor_for_mode(self, mode: int):
        """Update cursor based on drag mode."""
        cursors = {
            self.DRAG_NEW: Qt.CursorShape.CrossCursor,
            self.DRAG_MOVE: Qt.CursorShape.SizeAllCursor,
            self.DRAG_EDGE_LEFT: Qt.CursorShape.SizeHorCursor,
            self.DRAG_EDGE_RIGHT: Qt.CursorShape.SizeHorCursor,
            self.DRAG_EDGE_TOP: Qt.CursorShape.SizeVerCursor,
            self.DRAG_EDGE_BOTTOM: Qt.CursorShape.SizeVerCursor,
            self.DRAG_CORNER_TL: Qt.CursorShape.SizeFDiagCursor,
            self.DRAG_CORNER_BR: Qt.CursorShape.SizeFDiagCursor,
            self.DRAG_CORNER_TR: Qt.CursorShape.SizeBDiagCursor,
            self.DRAG_CORNER_BL: Qt.CursorShape.SizeBDiagCursor,
        }
        self.setCursor(QCursor(cursors.get(mode, Qt.CursorShape.CrossCursor)))

    def mousePressEvent(self, event):
        """Start crop selection or caption adjustment on mouse press."""
        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            # Clamp to video bounds
            x = max(0, min(scene_pos.x(), self._video_width))
            y = max(0, min(scene_pos.y(), self._video_height))

            if self._caption_mode:
                # Caption mode - only allow move/resize of existing caption
                self._drag_mode = self._hit_test_caption(QPointF(x, y))
                if self._drag_mode != self.DRAG_NONE:
                    self._drag_start = QPointF(x, y)
                    self._drag_current = QPointF(x, y)
                    self._original_rect = QRectF(self._current_caption_rect) if self._current_caption_rect else None
                    event.accept()
                    return
            elif self._crop_mode:
                self._drag_mode = self._hit_test(QPointF(x, y))
                self._drag_start = QPointF(x, y)
                self._drag_current = QPointF(x, y)

                # Store original rect for adjustment operations
                if self._drag_mode != self.DRAG_NEW and self._current_crop_rect:
                    self._original_rect = QRectF(self._current_crop_rect)
                else:
                    self._original_rect = None

                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Update crop/caption adjustment during drag or update cursor."""
        scene_pos = self.mapToScene(event.pos())
        x = max(0, min(scene_pos.x(), self._video_width))
        y = max(0, min(scene_pos.y(), self._video_height))

        if self._caption_mode and self._drag_start is not None:
            self._drag_current = QPointF(x, y)
            # Calculate caption rectangle based on drag mode
            rect = self._calculate_caption_rect()
            if rect:
                self.caption_rect_changed.emit(rect[0], rect[1], rect[2], rect[3])
            event.accept()
        elif self._caption_mode:
            # Update cursor based on hover position over caption
            mode = self._hit_test_caption(QPointF(x, y))
            if mode == self.DRAG_NONE:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            else:
                self._update_cursor_for_mode(mode)
            super().mouseMoveEvent(event)
        elif self._crop_mode and self._drag_start is not None:
            self._drag_current = QPointF(x, y)

            # Calculate rectangle based on drag mode
            rect = self._calculate_adjusted_rect()
            if rect:
                self.crop_rect_changed.emit(rect[0], rect[1], rect[2], rect[3])
            event.accept()
        elif self._crop_mode:
            # Update cursor based on hover position
            mode = self._hit_test(QPointF(x, y))
            self._update_cursor_for_mode(mode)
            super().mouseMoveEvent(event)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Finish crop/caption adjustment on mouse release."""
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            scene_pos = self.mapToScene(event.pos())
            x = max(0, min(scene_pos.x(), self._video_width))
            y = max(0, min(scene_pos.y(), self._video_height))
            self._drag_current = QPointF(x, y)

            if self._caption_mode:
                # Calculate final caption rectangle
                rect = self._calculate_caption_rect()
                if rect and rect[2] > 10 and rect[3] > 10:
                    self.caption_rect_changed.emit(rect[0], rect[1], rect[2], rect[3])
                    self.caption_drag_finished.emit()

                self._drag_mode = self.DRAG_NONE
                self._drag_start = None
                self._drag_current = None
                self._original_rect = None
                event.accept()
                return
            elif self._crop_mode:
                # Calculate final rectangle
                rect = self._calculate_adjusted_rect()
                if rect and rect[2] > 10 and rect[3] > 10:  # Minimum size threshold
                    self.crop_rect_changed.emit(rect[0], rect[1], rect[2], rect[3])
                    self.crop_drag_finished.emit()

                self._drag_mode = self.DRAG_NONE
                self._drag_start = None
                self._drag_current = None
                self._original_rect = None
                event.accept()
                return

        super().mouseReleaseEvent(event)

    def _calculate_adjusted_rect(self) -> tuple[float, float, float, float] | None:
        """Calculate crop rectangle based on current drag mode."""
        if self._drag_start is None or self._drag_current is None:
            return None

        if self._drag_mode == self.DRAG_NEW:
            return self._calculate_new_rect()
        elif self._drag_mode == self.DRAG_MOVE:
            return self._calculate_move_rect()
        else:
            return self._calculate_resize_rect()

    def _calculate_new_rect(self) -> tuple[float, float, float, float] | None:
        """Calculate rectangle for new selection."""
        x1, y1 = self._drag_start.x(), self._drag_start.y()
        x2, y2 = self._drag_current.x(), self._drag_current.y()

        raw_width = abs(x2 - x1)
        raw_height = abs(y2 - y1)

        if raw_width < 1 or raw_height < 1:
            return None

        # Apply aspect ratio constraint if set
        if self._aspect_ratio:
            target_ratio = self._aspect_ratio[0] / self._aspect_ratio[1]
            current_ratio = raw_width / raw_height

            if current_ratio > target_ratio:
                raw_width = raw_height * target_ratio
            else:
                raw_height = raw_width / target_ratio

        # Calculate top-left corner
        left = x1 if x2 >= x1 else x1 - raw_width
        top = y1 if y2 >= y1 else y1 - raw_height

        # Clamp to video bounds
        left = max(0, min(left, self._video_width - raw_width))
        top = max(0, min(top, self._video_height - raw_height))
        raw_width = min(raw_width, self._video_width - left)
        raw_height = min(raw_height, self._video_height - top)

        return (left, top, raw_width, raw_height)

    def _calculate_move_rect(self) -> tuple[float, float, float, float] | None:
        """Calculate rectangle for move operation."""
        if self._original_rect is None:
            return None

        dx = self._drag_current.x() - self._drag_start.x()
        dy = self._drag_current.y() - self._drag_start.y()

        new_left = self._original_rect.left() + dx
        new_top = self._original_rect.top() + dy
        width = self._original_rect.width()
        height = self._original_rect.height()

        # Clamp to video bounds
        new_left = max(0, min(new_left, self._video_width - width))
        new_top = max(0, min(new_top, self._video_height - height))

        return (new_left, new_top, width, height)

    def _calculate_resize_rect(self) -> tuple[float, float, float, float] | None:
        """Calculate rectangle for resize operation (edges/corners)."""
        if self._original_rect is None:
            return None

        orig = self._original_rect
        dx = self._drag_current.x() - self._drag_start.x()
        dy = self._drag_current.y() - self._drag_start.y()

        left, top = orig.left(), orig.top()
        right, bottom = orig.right(), orig.bottom()

        # Apply changes based on drag mode
        if self._drag_mode in (self.DRAG_EDGE_LEFT, self.DRAG_CORNER_TL, self.DRAG_CORNER_BL):
            left = min(orig.left() + dx, right - 20)  # Min width 20
        if self._drag_mode in (self.DRAG_EDGE_RIGHT, self.DRAG_CORNER_TR, self.DRAG_CORNER_BR):
            right = max(orig.right() + dx, left + 20)
        if self._drag_mode in (self.DRAG_EDGE_TOP, self.DRAG_CORNER_TL, self.DRAG_CORNER_TR):
            top = min(orig.top() + dy, bottom - 20)  # Min height 20
        if self._drag_mode in (self.DRAG_EDGE_BOTTOM, self.DRAG_CORNER_BL, self.DRAG_CORNER_BR):
            bottom = max(orig.bottom() + dy, top + 20)

        # Apply aspect ratio constraint for corner drags
        if self._aspect_ratio and self._drag_mode in (
            self.DRAG_CORNER_TL, self.DRAG_CORNER_TR, self.DRAG_CORNER_BL, self.DRAG_CORNER_BR
        ):
            target_ratio = self._aspect_ratio[0] / self._aspect_ratio[1]
            width = right - left
            height = bottom - top
            current_ratio = width / height if height > 0 else 1

            if current_ratio > target_ratio:
                # Adjust width
                new_width = height * target_ratio
                if self._drag_mode in (self.DRAG_CORNER_TL, self.DRAG_CORNER_BL):
                    left = right - new_width
                else:
                    right = left + new_width
            else:
                # Adjust height
                new_height = width / target_ratio
                if self._drag_mode in (self.DRAG_CORNER_TL, self.DRAG_CORNER_TR):
                    top = bottom - new_height
                else:
                    bottom = top + new_height

        # Clamp to video bounds
        left = max(0, left)
        top = max(0, top)
        right = min(self._video_width, right)
        bottom = min(self._video_height, bottom)

        width = right - left
        height = bottom - top

        if width < 10 or height < 10:
            return None

        return (left, top, width, height)

    def _calculate_caption_rect(self) -> tuple[float, float, float, float] | None:
        """Calculate caption rectangle based on current drag mode (move or resize)."""
        if self._drag_start is None or self._drag_current is None or self._original_rect is None:
            return None

        if self._drag_mode == self.DRAG_MOVE:
            return self._calculate_caption_move_rect()
        else:
            return self._calculate_caption_resize_rect()

    def _calculate_caption_move_rect(self) -> tuple[float, float, float, float] | None:
        """Calculate caption rectangle for move operation."""
        if self._original_rect is None:
            return None

        dx = self._drag_current.x() - self._drag_start.x()
        dy = self._drag_current.y() - self._drag_start.y()

        new_left = self._original_rect.left() + dx
        new_top = self._original_rect.top() + dy
        width = self._original_rect.width()
        height = self._original_rect.height()

        # Clamp to video bounds
        new_left = max(0, min(new_left, self._video_width - width))
        new_top = max(0, min(new_top, self._video_height - height))

        return (new_left, new_top, width, height)

    def _calculate_caption_resize_rect(self) -> tuple[float, float, float, float] | None:
        """Calculate caption rectangle for resize operation (all edges and corners)."""
        if self._original_rect is None:
            return None

        orig = self._original_rect
        dx = self._drag_current.x() - self._drag_start.x()
        dy = self._drag_current.y() - self._drag_start.y()

        left, top = orig.left(), orig.top()
        right, bottom = orig.right(), orig.bottom()

        # Allow resizing from all edges and corners
        if self._drag_mode in (self.DRAG_EDGE_LEFT, self.DRAG_CORNER_TL, self.DRAG_CORNER_BL):
            left = min(orig.left() + dx, right - 100)  # Min width 100 for captions
        if self._drag_mode in (self.DRAG_EDGE_RIGHT, self.DRAG_CORNER_TR, self.DRAG_CORNER_BR):
            right = max(orig.right() + dx, left + 100)
        if self._drag_mode in (self.DRAG_EDGE_TOP, self.DRAG_CORNER_TL, self.DRAG_CORNER_TR):
            top = min(orig.top() + dy, bottom - 40)  # Min height 40 for captions
        if self._drag_mode in (self.DRAG_EDGE_BOTTOM, self.DRAG_CORNER_BL, self.DRAG_CORNER_BR):
            bottom = max(orig.bottom() + dy, top + 40)

        # Clamp to video bounds
        left = max(0, left)
        top = max(0, top)
        right = min(self._video_width, right)
        bottom = min(self._video_height, bottom)

        width = right - left
        height = bottom - top

        if width < 100 or height < 40:
            return None

        return (left, top, width, height)


class VideoPlayer(QWidget):
    """
    Video player widget with playback controls and crop/pan support.

    Uses QGraphicsVideoItem for video display, allowing crop preview
    via viewport transforms.

    Signals:
        position_changed(int): Emitted when playback position changes (in ms)
        duration_changed(int): Emitted when video duration is known (in ms)
        crop_changed(CropConfig): Emitted when crop settings change
    """

    position_changed = Signal(int)
    duration_changed = Signal(int)
    crop_changed = Signal(object)  # CropConfig
    caption_settings_changed = Signal(object)  # CaptionSettings - emitted when user drags caption

    def __init__(self, parent=None):
        super().__init__(parent)

        self._video_path: Path | None = None
        self._duration_ms: int = 0
        self._seeking = False

        # Video dimensions (set when video loads)
        self._video_width: int = 1920
        self._video_height: int = 1080

        # Crop configuration
        self._crop_config = CropConfig()
        self._crop_mode = False

        # Caption overlay
        self._caption_settings = CaptionSettings()
        self._caption_tokens: list[Token] = []
        self._caption_chunks: list[list[Token]] = []
        self._caption_visible = True
        self._caption_mode = False

        self._setup_ui()
        self._setup_media_player()
        self._connect_signals()

    def _setup_ui(self):
        """Set up the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Graphics scene and view for video
        self._scene = QGraphicsScene()
        self._view = VideoView(self._scene)
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._view.setMinimumHeight(200)

        # Video item
        self._video_item = QGraphicsVideoItem()
        self._scene.addItem(self._video_item)

        # Crop overlay (semi-transparent dark areas outside crop region)
        self._crop_overlay_top = QGraphicsRectItem()
        self._crop_overlay_bottom = QGraphicsRectItem()
        self._crop_overlay_left = QGraphicsRectItem()
        self._crop_overlay_right = QGraphicsRectItem()
        self._crop_border = QGraphicsRectItem()

        overlay_brush = QBrush(QColor(0, 0, 0, 160))
        border_pen = QPen(QColor(33, 150, 243), 2)  # Blue border

        for overlay in [self._crop_overlay_top, self._crop_overlay_bottom,
                        self._crop_overlay_left, self._crop_overlay_right]:
            overlay.setBrush(overlay_brush)
            overlay.setPen(QPen(Qt.PenStyle.NoPen))
            overlay.setZValue(10)
            overlay.setVisible(False)
            self._scene.addItem(overlay)

        self._crop_border.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._crop_border.setPen(border_pen)
        self._crop_border.setZValue(11)
        self._crop_border.setVisible(False)
        self._scene.addItem(self._crop_border)

        # Caption overlay - background box and text
        self._caption_bg = QGraphicsRectItem()
        self._caption_bg.setBrush(QBrush(QColor(0, 0, 0, 180)))
        self._caption_bg.setPen(QPen(Qt.PenStyle.NoPen))
        self._caption_bg.setZValue(20)
        self._caption_bg.setVisible(False)
        self._scene.addItem(self._caption_bg)

        self._caption_text = QGraphicsTextItem()
        self._caption_text.setDefaultTextColor(QColor(255, 255, 255))
        self._caption_text.setZValue(21)
        self._caption_text.setVisible(False)
        self._scene.addItem(self._caption_text)

        layout.addWidget(self._view, stretch=1)

        # Time slider
        slider_layout = QHBoxLayout()
        slider_layout.setContentsMargins(4, 0, 4, 0)

        self._time_label = QLabel("00:00")
        self._time_label.setFixedWidth(50)
        slider_layout.addWidget(self._time_label)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        slider_layout.addWidget(self._slider, stretch=1)

        self._duration_label = QLabel("00:00")
        self._duration_label.setFixedWidth(50)
        self._duration_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        slider_layout.addWidget(self._duration_label)

        layout.addLayout(slider_layout)

        # Playback controls
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(4, 0, 4, 4)

        self._play_btn = QPushButton()
        self._play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self._play_btn.setFixedSize(40, 32)
        controls_layout.addWidget(self._play_btn)

        self._stop_btn = QPushButton()
        self._stop_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self._stop_btn.setFixedSize(40, 32)
        controls_layout.addWidget(self._stop_btn)

        controls_layout.addStretch()

        # Volume control
        volume_icon = QLabel("Vol")
        controls_layout.addWidget(volume_icon)

        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setFixedWidth(80)
        controls_layout.addWidget(self._volume_slider)

        layout.addLayout(controls_layout)

    def _setup_media_player(self):
        """Set up the media player."""
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(0.8)

        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_item)

        # Connect to native size changed to update scene rect
        self._video_item.nativeSizeChanged.connect(self._on_native_size_changed)

    def _connect_signals(self):
        """Connect internal signals."""
        # Button clicks
        self._play_btn.clicked.connect(self._toggle_play)
        self._stop_btn.clicked.connect(self._stop)

        # Slider interactions
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._slider.sliderMoved.connect(self._on_slider_moved)

        # Volume
        self._volume_slider.valueChanged.connect(self._on_volume_changed)

        # Media player signals
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)

        # Crop interaction signals from view
        self._view.crop_rect_changed.connect(self._on_crop_rect_changed)
        self._view.crop_drag_finished.connect(self._on_crop_drag_finished)

        # Caption interaction signals from view
        self._view.caption_rect_changed.connect(self._on_caption_rect_changed)
        self._view.caption_drag_finished.connect(self._on_caption_drag_finished)

    @Slot(QSizeF)
    def _on_native_size_changed(self, size: QSizeF):
        """Handle video size change when video loads."""
        if size.width() > 0 and size.height() > 0:
            self._video_width = int(size.width())
            self._video_height = int(size.height())

            # Set video item size
            self._video_item.setSize(size)

            # Update scene rect to match video
            self._scene.setSceneRect(0, 0, size.width(), size.height())

            # Update view's video dimensions for crop interaction
            self._view.set_video_dimensions(self._video_width, self._video_height)

            # Fit view to scene
            self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

            # Update crop overlay if visible
            if self._crop_mode:
                self._update_crop_overlay()

    def _update_crop_overlay(self):
        """Update the crop overlay rectangles based on current crop config."""
        if not self._crop_mode:
            return

        video_w = self._video_width
        video_h = self._video_height

        # Get crop rectangle
        crop_x, crop_y, crop_w, crop_h = self._crop_config.get_crop_rect(video_w, video_h)

        # Update overlay rectangles (dark areas outside crop)
        # Top overlay
        self._crop_overlay_top.setRect(0, 0, video_w, crop_y)
        # Bottom overlay
        self._crop_overlay_bottom.setRect(0, crop_y + crop_h, video_w, video_h - crop_y - crop_h)
        # Left overlay
        self._crop_overlay_left.setRect(0, crop_y, crop_x, crop_h)
        # Right overlay
        self._crop_overlay_right.setRect(crop_x + crop_w, crop_y, video_w - crop_x - crop_w, crop_h)

        # Update border rectangle
        self._crop_border.setRect(crop_x, crop_y, crop_w, crop_h)

        # Sync current crop rect to view for adjustment hit testing
        self._view.set_current_crop_rect(QRectF(crop_x, crop_y, crop_w, crop_h))

    # Public API

    def load_video(self, path: Path) -> None:
        """Load a video file."""
        self._video_path = path
        self._player.setSource(QUrl.fromLocalFile(str(path)))

    def play(self) -> None:
        """Start playback."""
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._player.play()

    def pause(self) -> None:
        """Pause playback."""
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()

    def toggle_play(self) -> None:
        """Toggle between play and pause."""
        self._toggle_play()

    def stop(self) -> None:
        """Stop playback and reset position."""
        self._stop()

    def seek(self, position_ms: int) -> None:
        """Seek to a specific position in milliseconds."""
        self._player.setPosition(position_ms)

    def seek_seconds(self, seconds: float) -> None:
        """Seek to a specific position in seconds."""
        self.seek(int(seconds * 1000))

    def get_position_ms(self) -> int:
        """Get current position in milliseconds."""
        return self._player.position()

    def get_position_seconds(self) -> float:
        """Get current position in seconds."""
        return self._player.position() / 1000.0

    def get_duration_ms(self) -> int:
        """Get video duration in milliseconds."""
        return self._duration_ms

    def get_duration_seconds(self) -> float:
        """Get video duration in seconds."""
        return self._duration_ms / 1000.0

    def is_playing(self) -> bool:
        """Check if video is currently playing."""
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def jump_forward(self, seconds: float = 5.0) -> None:
        """Jump forward by specified seconds."""
        new_pos = min(self._player.position() + int(seconds * 1000), self._duration_ms)
        self._player.setPosition(new_pos)

    def jump_backward(self, seconds: float = 5.0) -> None:
        """Jump backward by specified seconds."""
        new_pos = max(self._player.position() - int(seconds * 1000), 0)
        self._player.setPosition(new_pos)

    # Crop API

    def get_video_dimensions(self) -> tuple[int, int]:
        """Get the video dimensions (width, height)."""
        return self._video_width, self._video_height

    def set_crop_mode(self, enabled: bool) -> None:
        """Enable or disable crop editing mode."""
        self._crop_mode = enabled

        # Enable/disable mouse interaction on the view
        self._view.set_crop_interaction(enabled)

        if enabled:
            # In crop mode: show semi-transparent overlays for editing
            overlay_brush = QBrush(QColor(0, 0, 0, 160))
            for overlay in [self._crop_overlay_top, self._crop_overlay_bottom,
                            self._crop_overlay_left, self._crop_overlay_right]:
                overlay.setBrush(overlay_brush)
                overlay.setVisible(True)
            self._crop_border.setVisible(True)
            # Show full video for editing
            self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            self._update_crop_overlay()
        else:
            # When exiting crop mode: hide border, apply opaque mask for preview
            self._crop_border.setVisible(False)
            self._apply_crop_view()

    def is_crop_mode(self) -> bool:
        """Check if crop mode is active."""
        return self._crop_mode

    def set_crop_config(self, config: CropConfig) -> None:
        """Set the crop configuration."""
        self._crop_config = config
        if self._crop_mode:
            self._update_crop_overlay()
        else:
            self._apply_crop_view()
        self.crop_changed.emit(config)

    def get_crop_config(self) -> CropConfig:
        """Get the current crop configuration."""
        return self._crop_config

    def _apply_crop_view(self):
        """Apply crop by masking out areas outside the crop region."""
        if self._crop_config.is_default:
            # Show full video - hide all mask overlays
            for overlay in [self._crop_overlay_top, self._crop_overlay_bottom,
                            self._crop_overlay_left, self._crop_overlay_right]:
                overlay.setVisible(False)
            self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        else:
            # Show crop preview with fully opaque black mask hiding non-cropped areas
            crop_x, crop_y, crop_w, crop_h = self._crop_config.get_crop_rect(
                self._video_width, self._video_height
            )

            # Use fully opaque black to completely hide non-cropped areas
            mask_brush = QBrush(QColor(0, 0, 0, 255))

            # Update overlay rectangles to mask out areas outside crop
            self._crop_overlay_top.setBrush(mask_brush)
            self._crop_overlay_top.setRect(0, 0, self._video_width, crop_y)
            self._crop_overlay_top.setVisible(True)

            self._crop_overlay_bottom.setBrush(mask_brush)
            self._crop_overlay_bottom.setRect(0, crop_y + crop_h, self._video_width, self._video_height - crop_y - crop_h)
            self._crop_overlay_bottom.setVisible(True)

            self._crop_overlay_left.setBrush(mask_brush)
            self._crop_overlay_left.setRect(0, crop_y, crop_x, crop_h)
            self._crop_overlay_left.setVisible(True)

            self._crop_overlay_right.setBrush(mask_brush)
            self._crop_overlay_right.setRect(crop_x + crop_w, crop_y, self._video_width - crop_x - crop_w, crop_h)
            self._crop_overlay_right.setVisible(True)

            # Zoom to crop region
            crop_rect = QRectF(crop_x, crop_y, crop_w, crop_h)
            self._view.fitInView(crop_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def adjust_crop_size(self, width_delta: float, height_delta: float) -> None:
        """Adjust crop size by delta values (normalized)."""
        new_width = max(0.1, min(1.0, self._crop_config.width + width_delta))
        new_height = max(0.1, min(1.0, self._crop_config.height + height_delta))
        self._crop_config.width = new_width
        self._crop_config.height = new_height

        # Clamp pan to valid range
        self._clamp_pan()

        if self._crop_mode:
            self._update_crop_overlay()
        self.crop_changed.emit(self._crop_config)

    def adjust_pan(self, pan_x_delta: float, pan_y_delta: float) -> None:
        """Adjust pan offset by delta values."""
        self._crop_config.pan_x += pan_x_delta
        self._crop_config.pan_y += pan_y_delta
        self._clamp_pan()

        if self._crop_mode:
            self._update_crop_overlay()
        else:
            self._apply_crop_view()
        self.crop_changed.emit(self._crop_config)

    def _clamp_pan(self):
        """Clamp pan values to valid range (-1 to 1)."""
        self._crop_config.pan_x = max(-1.0, min(1.0, self._crop_config.pan_x))
        self._crop_config.pan_y = max(-1.0, min(1.0, self._crop_config.pan_y))

    def reset_crop(self) -> None:
        """Reset crop to default (full frame)."""
        self._crop_config = CropConfig()
        self._view.set_current_crop_rect(None)  # Clear adjustment rect
        if self._crop_mode:
            self._update_crop_overlay()
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.crop_changed.emit(self._crop_config)

    def set_aspect_ratio(self, ratio: tuple[int, int] | None) -> None:
        """Set the aspect ratio constraint for crop selection.

        Args:
            ratio: Tuple of (width, height) e.g., (16, 9) or None for free aspect
        """
        self._view.set_aspect_ratio(ratio)

    def set_crop_from_rect(self, x: float, y: float, width: float, height: float) -> None:
        """Set crop configuration from pixel coordinates.

        Args:
            x: Left edge in pixels
            y: Top edge in pixels
            width: Width in pixels
            height: Height in pixels
        """
        # Convert pixel coordinates to normalized CropConfig
        norm_width = width / self._video_width
        norm_height = height / self._video_height

        # Calculate pan based on center position
        # Center of crop in pixels
        center_x = x + width / 2
        center_y = y + height / 2

        # Center of video
        video_center_x = self._video_width / 2
        video_center_y = self._video_height / 2

        # Available pan range (how far center can move from video center)
        max_pan_x = (self._video_width - width) / 2
        max_pan_y = (self._video_height - height) / 2

        # Calculate normalized pan (-1 to 1)
        if max_pan_x > 0:
            pan_x = (center_x - video_center_x) / max_pan_x
        else:
            pan_x = 0.0

        if max_pan_y > 0:
            pan_y = (center_y - video_center_y) / max_pan_y
        else:
            pan_y = 0.0

        # Clamp values
        pan_x = max(-1.0, min(1.0, pan_x))
        pan_y = max(-1.0, min(1.0, pan_y))

        self._crop_config = CropConfig(
            width=norm_width,
            height=norm_height,
            pan_x=pan_x,
            pan_y=pan_y
        )

        self._update_crop_overlay()
        self.crop_changed.emit(self._crop_config)

    @Slot(float, float, float, float)
    def _on_crop_rect_changed(self, x: float, y: float, width: float, height: float):
        """Handle crop rectangle changes from mouse drag (live preview)."""
        # Update overlay directly from pixel coordinates for smooth preview
        self._crop_overlay_top.setRect(0, 0, self._video_width, y)
        self._crop_overlay_bottom.setRect(0, y + height, self._video_width, self._video_height - y - height)
        self._crop_overlay_left.setRect(0, y, x, height)
        self._crop_overlay_right.setRect(x + width, y, self._video_width - x - width, height)
        self._crop_border.setRect(x, y, width, height)

        # Keep view's current crop rect in sync for subsequent adjustments
        self._view.set_current_crop_rect(QRectF(x, y, width, height))

    @Slot()
    def _on_crop_drag_finished(self):
        """Handle crop drag completion - convert to CropConfig."""
        # Get the current crop border rect and convert to CropConfig
        rect = self._crop_border.rect()
        if rect.width() > 10 and rect.height() > 10:
            self.set_crop_from_rect(rect.x(), rect.y(), rect.width(), rect.height())

    # Private slots

    @Slot()
    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    @Slot()
    def _stop(self):
        self._player.stop()
        self._player.setPosition(0)

    @Slot()
    def _on_slider_pressed(self):
        self._seeking = True

    @Slot()
    def _on_slider_released(self):
        self._seeking = False
        self._player.setPosition(self._slider.value())

    @Slot(int)
    def _on_slider_moved(self, value: int):
        self._time_label.setText(format_time(value))

    @Slot(int)
    def _on_volume_changed(self, value: int):
        self._audio_output.setVolume(value / 100.0)

    @Slot(int)
    def _on_position_changed(self, position: int):
        if not self._seeking:
            self._slider.setValue(position)
            self._time_label.setText(format_time(position))
        self.position_changed.emit(position)

    @Slot(int)
    def _on_duration_changed(self, duration: int):
        self._duration_ms = duration
        self._slider.setRange(0, duration)
        self._duration_label.setText(format_time(duration))
        self.duration_changed.emit(duration)

    @Slot(QMediaPlayer.PlaybackState)
    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self._play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    # Caption API

    def set_caption_tokens(self, tokens: list[Token]) -> None:
        """Set the tokens for caption display and build chunks."""
        self._caption_tokens = tokens
        self._caption_chunks = self._chunk_tokens(tokens, max_words=15)

    def _chunk_tokens(self, tokens: list[Token], max_words: int = 15, gap_threshold: float = 1.5) -> list[list[Token]]:
        """Group tokens into display chunks based on max words and silence gaps."""
        if not tokens:
            return []

        chunks = []
        current_chunk: list[Token] = []

        for i, token in enumerate(tokens):
            current_chunk.append(token)

            should_end_chunk = False

            if len(current_chunk) >= max_words:
                should_end_chunk = True

            if i < len(tokens) - 1:
                gap = tokens[i + 1].start - token.end
                if gap > gap_threshold:
                    should_end_chunk = True

            if i == len(tokens) - 1:
                should_end_chunk = True

            if should_end_chunk and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []

        return chunks

    def set_caption_settings(self, settings: CaptionSettings) -> None:
        """Set caption display settings."""
        self._caption_settings = settings
        self._caption_visible = settings.show_preview
        self._update_caption_style()

        # Update visibility
        if self._caption_tokens and self._caption_visible:
            self.update_caption(self._player.position() / 1000.0)
        else:
            self._caption_text.setVisible(False)
            self._caption_bg.setVisible(False)

    def get_caption_settings(self) -> CaptionSettings:
        """Get current caption settings."""
        return self._caption_settings

    def set_caption_visible(self, visible: bool) -> None:
        """Set whether captions are visible."""
        self._caption_visible = visible
        if not visible:
            self._caption_text.setVisible(False)
            self._caption_bg.setVisible(False)

    def _update_caption_style(self) -> None:
        """Update caption font and styling based on settings."""
        font = QFont(self._caption_settings.font_family, self._caption_settings.font_size)

        # Apply font weight
        weight_map = {
            "regular": QFont.Weight.Normal,
            "medium": QFont.Weight.Medium,
            "semi-bold": QFont.Weight.DemiBold,
            "bold": QFont.Weight.Bold,
            "extra-bold": QFont.Weight.ExtraBold,
        }
        weight = weight_map.get(self._caption_settings.font_weight, QFont.Weight.Bold)
        font.setWeight(weight)
        self._caption_text.setFont(font)

        # Apply text color
        if self._caption_settings.text_color == "black":
            self._caption_text.setDefaultTextColor(QColor(0, 0, 0))
        else:
            self._caption_text.setDefaultTextColor(QColor(255, 255, 255))

    def update_caption(self, time_seconds: float) -> None:
        """Update caption display based on current playback time."""
        if not self._caption_chunks or not self._caption_visible:
            self._caption_text.setVisible(False)
            self._caption_bg.setVisible(False)
            self._view.set_current_caption_rect(None)
            return

        # Find the current chunk and word index
        current_text = self._get_caption_text_at_time(time_seconds)

        if not current_text:
            self._caption_text.setVisible(False)
            self._caption_bg.setVisible(False)
            self._view.set_current_caption_rect(None)
            return

        # Get the fixed box dimensions from settings
        box_x, box_y, box_w, box_h = self._caption_settings.get_box_pixels(
            self._video_width, self._video_height
        )

        # Clamp to video bounds
        box_x = max(0, min(box_x, self._video_width - box_w))
        box_y = max(0, min(box_y, self._video_height - box_h))

        # Set up text with word wrapping within the fixed box
        padding = 10
        text_width = box_w - padding * 2

        # Set the text width for word wrapping
        self._caption_text.setTextWidth(text_width)
        self._caption_text.setPlainText(current_text)
        self._caption_text.setVisible(True)

        # Position text inside the box with padding
        self._caption_text.setPos(box_x + padding, box_y + padding / 2)

        # Update background to fixed size
        bg_rect = QRectF(box_x, box_y, box_w, box_h)
        self._caption_bg.setRect(bg_rect)
        self._caption_bg.setVisible(self._caption_settings.show_background)

        # Sync caption rect to view for drag interaction
        self._view.set_current_caption_rect(bg_rect)

    def set_caption_mode(self, enabled: bool) -> None:
        """Enable or disable caption editing mode (drag to move/resize)."""
        self._caption_mode = enabled
        self._view.set_caption_interaction(enabled)

        if enabled:
            # Make sure caption is visible for editing
            if self._caption_tokens and not self._caption_visible:
                self._caption_visible = True
                self.update_caption(self._player.position() / 1000.0)
            # Show a blue border around caption to indicate it's draggable
            self._caption_bg.setPen(QPen(QColor(33, 150, 243), 2))  # Blue border
        else:
            # Remove border when not in caption mode
            self._caption_bg.setPen(QPen(Qt.PenStyle.NoPen))

    def is_caption_mode(self) -> bool:
        """Check if caption mode is active."""
        return self._caption_mode

    @Slot(float, float, float, float)
    def _on_caption_rect_changed(self, x: float, y: float, width: float, height: float):
        """Handle caption rectangle changes from mouse drag (live preview)."""
        # Update the caption background and text position directly for smooth preview
        padding = 10
        text_x = x + padding
        text_y = y + padding / 2

        # Update text width for word wrapping when box is resized
        text_width = width - padding * 2
        self._caption_text.setTextWidth(text_width)
        self._caption_text.setPos(text_x, text_y)
        self._caption_bg.setRect(QRectF(x, y, width, height))

        # Update the view's current caption rect for subsequent adjustments
        self._view.set_current_caption_rect(QRectF(x, y, width, height))

    @Slot()
    def _on_caption_drag_finished(self):
        """Handle caption drag completion - convert to CaptionSettings."""
        # Get the current caption background rect and convert to normalized settings
        rect = self._caption_bg.rect()
        if rect.width() > 10 and rect.height() > 10:
            # Calculate center_x (center of box) and bottom_y (bottom of box)
            center_x = rect.x() + rect.width() / 2
            bottom_y = rect.y() + rect.height()

            # Convert to normalized coordinates
            pos_x = center_x / self._video_width
            pos_y = bottom_y / self._video_height
            box_width = rect.width() / self._video_width
            box_height = rect.height() / self._video_height

            # Update settings
            self._caption_settings.pos_x = max(0.0, min(1.0, pos_x))
            self._caption_settings.pos_y = max(0.0, min(1.0, pos_y))
            self._caption_settings.box_width = max(0.1, min(1.0, box_width))
            self._caption_settings.box_height = max(0.03, min(0.3, box_height))

            # Emit signal so main window can update the settings panel and session
            self.caption_settings_changed.emit(self._caption_settings)

    def _get_caption_text_at_time(self, time_seconds: float) -> str:
        """Get the caption text to display at the given time."""
        for chunk in self._caption_chunks:
            if not chunk:
                continue

            chunk_start = chunk[0].start
            chunk_end = chunk[-1].end + 0.1  # Small buffer

            if chunk_start <= time_seconds <= chunk_end:
                # Find how many words to show (accumulating effect)
                accumulated = []
                for token in chunk:
                    if token.start <= time_seconds:
                        accumulated.append(token.text.strip())

                if accumulated:
                    return " ".join(accumulated)

        return ""
