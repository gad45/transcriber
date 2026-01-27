"""Caption settings panel widget."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QComboBox,
    QSlider, QCheckBox, QGroupBox
)
from PySide6.QtGui import QFontDatabase
from PySide6.QtCore import Qt

from .models import CaptionSettings


class CaptionSettingsPanel(QWidget):
    """Widget for configuring caption appearance."""

    settings_changed = Signal(object)  # CaptionSettings

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = CaptionSettings()
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        """Set up the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

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

        # Position
        pos_layout = QHBoxLayout()
        pos_label = QLabel("Position:")
        pos_label.setFixedWidth(80)
        pos_layout.addWidget(pos_label)

        self._position_combo = QComboBox()
        self._position_combo.addItems(["Bottom", "Center", "Top"])
        pos_layout.addWidget(self._position_combo, stretch=1)

        layout.addLayout(pos_layout)

        # Vertical offset
        offset_layout = QHBoxLayout()
        offset_label = QLabel("Offset:")
        offset_label.setFixedWidth(80)
        offset_layout.addWidget(offset_label)

        self._offset_spin = QSpinBox()
        self._offset_spin.setRange(10, 300)
        self._offset_spin.setValue(60)
        self._offset_spin.setSuffix(" px")
        offset_layout.addWidget(self._offset_spin)

        self._offset_slider = QSlider(Qt.Orientation.Horizontal)
        self._offset_slider.setRange(10, 300)
        self._offset_slider.setValue(60)
        offset_layout.addWidget(self._offset_slider, stretch=1)

        layout.addLayout(offset_layout)

        layout.addStretch()

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
        """)

    def _connect_signals(self):
        """Connect internal signals."""
        # Sync spinbox and slider
        self._size_spin.valueChanged.connect(self._size_slider.setValue)
        self._size_slider.valueChanged.connect(self._size_spin.setValue)
        self._offset_spin.valueChanged.connect(self._offset_slider.setValue)
        self._offset_slider.valueChanged.connect(self._offset_spin.setValue)

        # Emit settings changed
        self._preview_check.toggled.connect(self._on_setting_changed)
        self._size_spin.valueChanged.connect(self._on_setting_changed)
        self._font_combo.currentTextChanged.connect(self._on_setting_changed)
        self._position_combo.currentTextChanged.connect(self._on_setting_changed)
        self._offset_spin.valueChanged.connect(self._on_setting_changed)

    def _on_setting_changed(self):
        """Handle any setting change."""
        self._settings = CaptionSettings(
            font_size=self._size_spin.value(),
            font_family=self._font_combo.currentText(),
            position=self._position_combo.currentText().lower(),
            vertical_offset=float(self._offset_spin.value()),
            show_preview=self._preview_check.isChecked()
        )
        self.settings_changed.emit(self._settings)

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
        self._position_combo.blockSignals(True)
        self._offset_spin.blockSignals(True)
        self._offset_slider.blockSignals(True)
        self._preview_check.blockSignals(True)

        self._size_spin.setValue(settings.font_size)
        self._size_slider.setValue(settings.font_size)

        font_idx = self._font_combo.findText(settings.font_family)
        if font_idx >= 0:
            self._font_combo.setCurrentIndex(font_idx)

        pos_map = {"bottom": 0, "center": 1, "top": 2}
        self._position_combo.setCurrentIndex(pos_map.get(settings.position, 0))

        self._offset_spin.setValue(int(settings.vertical_offset))
        self._offset_slider.setValue(int(settings.vertical_offset))
        self._preview_check.setChecked(settings.show_preview)

        # Unblock signals
        self._size_spin.blockSignals(False)
        self._size_slider.blockSignals(False)
        self._font_combo.blockSignals(False)
        self._position_combo.blockSignals(False)
        self._offset_spin.blockSignals(False)
        self._offset_slider.blockSignals(False)
        self._preview_check.blockSignals(False)
