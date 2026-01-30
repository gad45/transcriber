"""Caption settings panel widget."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QComboBox,
    QSlider, QCheckBox, QPushButton
)
from PySide6.QtGui import QFontDatabase
from PySide6.QtCore import Qt

from .models import CaptionSettings


class CaptionSettingsPanel(QWidget):
    """Widget for configuring caption appearance."""

    settings_changed = Signal(object)  # CaptionSettings
    move_caption_requested = Signal(bool)  # True to enable move mode, False to disable
    regenerate_requested = Signal()  # Emitted when user wants to refresh captions from edited text

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = CaptionSettings()
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        """Set up the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Preview toggle
        preview_layout = QHBoxLayout()
        self._preview_check = QCheckBox("Show caption preview")
        self._preview_check.setChecked(True)
        preview_layout.addWidget(self._preview_check)
        preview_layout.addStretch()
        layout.addLayout(preview_layout)

        # Font size
        size_layout = QHBoxLayout()
        size_label = QLabel("Font size:")
        size_label.setFixedWidth(80)
        size_layout.addWidget(size_label)

        self._size_spin = QSpinBox()
        self._size_spin.setRange(12, 72)
        self._size_spin.setValue(24)
        self._size_spin.setSuffix(" pt")
        size_layout.addWidget(self._size_spin)

        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setRange(12, 72)
        self._size_slider.setValue(24)
        size_layout.addWidget(self._size_slider, stretch=1)

        layout.addLayout(size_layout)

        # Font family
        font_layout = QHBoxLayout()
        font_label = QLabel("Font:")
        font_label.setFixedWidth(80)
        font_layout.addWidget(font_label)

        self._font_combo = QComboBox()
        # Get available fonts
        fonts = QFontDatabase.families()
        # Prioritize common fonts
        preferred = ["Arial", "Helvetica", "Verdana", "Roboto", "Open Sans", "SF Pro", "Segoe UI"]
        sorted_fonts = []
        for pref in preferred:
            if pref in fonts:
                sorted_fonts.append(pref)
        for font in sorted(fonts):
            if font not in sorted_fonts:
                sorted_fonts.append(font)
        self._font_combo.addItems(sorted_fonts)
        # Set Arial as default if available
        arial_idx = self._font_combo.findText("Arial")
        if arial_idx >= 0:
            self._font_combo.setCurrentIndex(arial_idx)
        font_layout.addWidget(self._font_combo, stretch=1)

        layout.addLayout(font_layout)

        # Font weight
        weight_layout = QHBoxLayout()
        weight_label = QLabel("Weight:")
        weight_label.setFixedWidth(80)
        weight_layout.addWidget(weight_label)

        self._weight_combo = QComboBox()
        self._weight_combo.addItems(["Regular", "Medium", "Semi-Bold", "Bold", "Extra-Bold"])
        self._weight_combo.setCurrentText("Bold")
        weight_layout.addWidget(self._weight_combo, stretch=1)

        layout.addLayout(weight_layout)

        # Text color
        color_layout = QHBoxLayout()
        color_label = QLabel("Text color:")
        color_label.setFixedWidth(80)
        color_layout.addWidget(color_label)

        self._color_combo = QComboBox()
        self._color_combo.addItems(["White", "Black"])
        color_layout.addWidget(self._color_combo, stretch=1)

        layout.addLayout(color_layout)

        # Background toggle
        bg_layout = QHBoxLayout()
        bg_label = QLabel("")
        bg_label.setFixedWidth(80)
        bg_layout.addWidget(bg_label)

        self._bg_check = QCheckBox("Show background")
        self._bg_check.setChecked(True)
        self._bg_check.setToolTip("Show semi-transparent background behind caption text")
        bg_layout.addWidget(self._bg_check)
        bg_layout.addStretch()

        layout.addLayout(bg_layout)

        # Position section with move button
        pos_layout = QHBoxLayout()
        pos_label = QLabel("Position:")
        pos_label.setFixedWidth(80)
        pos_layout.addWidget(pos_label)

        self._move_btn = QPushButton("Drag to Move")
        self._move_btn.setCheckable(True)
        self._move_btn.setToolTip("Click and drag the caption on the video to reposition it")
        pos_layout.addWidget(self._move_btn, stretch=1)

        layout.addLayout(pos_layout)

        # Position display (read-only, shows normalized position)
        pos_display_layout = QHBoxLayout()
        pos_display_label = QLabel("")  # Empty label for alignment
        pos_display_label.setFixedWidth(80)
        pos_display_layout.addWidget(pos_display_label)

        self._pos_display = QLabel("Center: 50%, Bottom: 92%")
        self._pos_display.setStyleSheet("color: #888; font-size: 11px;")
        pos_display_layout.addWidget(self._pos_display, stretch=1)

        layout.addLayout(pos_display_layout)

        # Reset position button
        reset_layout = QHBoxLayout()
        reset_label = QLabel("")
        reset_label.setFixedWidth(80)
        reset_layout.addWidget(reset_label)

        self._reset_pos_btn = QPushButton("Reset Position")
        self._reset_pos_btn.setToolTip("Reset caption to default centered bottom position")
        reset_layout.addWidget(self._reset_pos_btn, stretch=1)

        layout.addLayout(reset_layout)

        # Regenerate captions button
        regen_layout = QHBoxLayout()
        regen_label = QLabel("")
        regen_label.setFixedWidth(80)
        regen_layout.addWidget(regen_label)

        self._regen_btn = QPushButton("Refresh Captions")
        self._regen_btn.setToolTip("Regenerate caption preview from edited transcript text")
        regen_layout.addWidget(self._regen_btn, stretch=1)

        layout.addLayout(regen_layout)

        # Apply dark theme styling
        self.setStyleSheet("""
            QLabel {
                color: #fff;
            }
            QSpinBox, QComboBox {
                background-color: #3d3d3d;
                color: #fff;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 4px;
            }
            QSlider::groove:horizontal {
                background: #3d3d3d;
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #2196f3;
                width: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }
            QCheckBox {
                color: #fff;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }
            QCheckBox::indicator:unchecked {
                border: 2px solid #555;
                background: #3d3d3d;
            }
            QCheckBox::indicator:checked {
                border: 2px solid #2196f3;
                background: #2196f3;
            }
            QPushButton {
                background-color: #3d3d3d;
                color: #fff;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
            }
            QPushButton:checked {
                background-color: #2196f3;
                border-color: #2196f3;
            }
            QPushButton:pressed {
                background-color: #1976d2;
            }
        """)

    def _connect_signals(self):
        """Connect internal signals."""
        # Sync spinbox and slider
        self._size_spin.valueChanged.connect(self._size_slider.setValue)
        self._size_slider.valueChanged.connect(self._size_spin.setValue)

        # Emit settings changed
        self._preview_check.toggled.connect(self._on_setting_changed)
        self._size_spin.valueChanged.connect(self._on_setting_changed)
        self._font_combo.currentTextChanged.connect(self._on_setting_changed)
        self._weight_combo.currentTextChanged.connect(self._on_setting_changed)
        self._color_combo.currentTextChanged.connect(self._on_setting_changed)
        self._bg_check.toggled.connect(self._on_setting_changed)

        # Move button
        self._move_btn.toggled.connect(self._on_move_toggled)

        # Reset position
        self._reset_pos_btn.clicked.connect(self._on_reset_position)

        # Regenerate captions
        self._regen_btn.clicked.connect(self.regenerate_requested.emit)

    def _on_setting_changed(self):
        """Handle any setting change."""
        self._settings.font_size = self._size_spin.value()
        self._settings.font_family = self._font_combo.currentText()
        self._settings.font_weight = self._weight_combo.currentText().lower()
        self._settings.show_preview = self._preview_check.isChecked()
        self._settings.text_color = self._color_combo.currentText().lower()
        self._settings.show_background = self._bg_check.isChecked()
        self.settings_changed.emit(self._settings)

    def _on_move_toggled(self, checked: bool):
        """Handle move button toggle."""
        self.move_caption_requested.emit(checked)

    def _on_reset_position(self):
        """Reset caption position and size to default."""
        self._settings.pos_x = 0.5
        self._settings.pos_y = 0.92
        self._settings.box_width = 0.6
        self._settings.box_height = 0.07
        self._update_position_display()
        self.settings_changed.emit(self._settings)

    def _update_position_display(self):
        """Update the position display label."""
        x_pct = int(self._settings.pos_x * 100)
        y_pct = int(self._settings.pos_y * 100)
        w_pct = int(self._settings.box_width * 100)
        h_pct = int(self._settings.box_height * 100)
        self._pos_display.setText(f"Pos: {x_pct}%, {y_pct}% | Size: {w_pct}% x {h_pct}%")

    def get_settings(self) -> CaptionSettings:
        """Get the current caption settings."""
        return self._settings

    def set_settings(self, settings: CaptionSettings):
        """Set the caption settings."""
        self._settings = settings

        # Block signals to prevent multiple emissions
        self._size_spin.blockSignals(True)
        self._size_slider.blockSignals(True)
        self._font_combo.blockSignals(True)
        self._weight_combo.blockSignals(True)
        self._preview_check.blockSignals(True)
        self._color_combo.blockSignals(True)
        self._bg_check.blockSignals(True)

        self._size_spin.setValue(settings.font_size)
        self._size_slider.setValue(settings.font_size)

        font_idx = self._font_combo.findText(settings.font_family)
        if font_idx >= 0:
            self._font_combo.setCurrentIndex(font_idx)

        # Set font weight (convert from lowercase with hyphens to title case)
        weight_text = settings.font_weight.replace("-", "-").title().replace("-", "-")
        weight_idx = self._weight_combo.findText(weight_text)
        if weight_idx >= 0:
            self._weight_combo.setCurrentIndex(weight_idx)

        self._preview_check.setChecked(settings.show_preview)

        # Set text color
        color_idx = self._color_combo.findText(settings.text_color.capitalize())
        if color_idx >= 0:
            self._color_combo.setCurrentIndex(color_idx)

        # Set background toggle
        self._bg_check.setChecked(settings.show_background)

        # Update position display
        self._update_position_display()

        # Unblock signals
        self._size_spin.blockSignals(False)
        self._size_slider.blockSignals(False)
        self._font_combo.blockSignals(False)
        self._weight_combo.blockSignals(False)
        self._preview_check.blockSignals(False)
        self._color_combo.blockSignals(False)
        self._bg_check.blockSignals(False)

    def set_move_mode(self, enabled: bool):
        """Set the move button state (called when mode changes externally)."""
        self._move_btn.blockSignals(True)
        self._move_btn.setChecked(enabled)
        self._move_btn.blockSignals(False)

    def update_position_from_drag(self, settings: CaptionSettings):
        """Update settings and display when position is changed by dragging."""
        self._settings = settings
        self._update_position_display()
