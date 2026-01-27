"""Settings dialog for API keys configuration."""

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QFormLayout, QMessageBox
)


class SettingsDialog(QDialog):
    """Dialog for configuring API keys and other settings."""

    settings_changed = Signal()

    # Settings file location
    SETTINGS_FILE = Path.home() / ".video_editor_settings"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(500)
        self._setup_ui()
        self._load_settings()
        self._apply_dark_theme()

    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # API Keys group
        api_group = QGroupBox("API Keys")
        api_layout = QFormLayout(api_group)
        api_layout.setSpacing(12)

        # Soniox API Key
        self._soniox_key = QLineEdit()
        self._soniox_key.setPlaceholderText("Enter your Soniox API key")
        self._soniox_key.setEchoMode(QLineEdit.EchoMode.Password)
        soniox_layout = QHBoxLayout()
        soniox_layout.addWidget(self._soniox_key)
        self._soniox_show_btn = QPushButton("Show")
        self._soniox_show_btn.setFixedWidth(60)
        self._soniox_show_btn.setCheckable(True)
        self._soniox_show_btn.toggled.connect(self._toggle_soniox_visibility)
        soniox_layout.addWidget(self._soniox_show_btn)

        soniox_label = QLabel("Soniox API Key:")
        soniox_label.setToolTip("Required for speech transcription.\nGet your key at: https://soniox.com")
        api_layout.addRow(soniox_label, soniox_layout)

        # Gemini API Key
        self._gemini_key = QLineEdit()
        self._gemini_key.setPlaceholderText("Enter your Gemini API key")
        self._gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
        gemini_layout = QHBoxLayout()
        gemini_layout.addWidget(self._gemini_key)
        self._gemini_show_btn = QPushButton("Show")
        self._gemini_show_btn.setFixedWidth(60)
        self._gemini_show_btn.setCheckable(True)
        self._gemini_show_btn.toggled.connect(self._toggle_gemini_visibility)
        gemini_layout.addWidget(self._gemini_show_btn)

        gemini_label = QLabel("Gemini API Key:")
        gemini_label.setToolTip("Required for intelligent take selection.\nGet your key at: https://makersuite.google.com/app/apikey")
        api_layout.addRow(gemini_label, gemini_layout)

        layout.addWidget(api_group)

        # Help text
        help_text = QLabel(
            "API keys are stored locally and never shared.\n"
            "• Soniox: Required for speech-to-text transcription\n"
            "• Gemini: Required for intelligent take selection (optional but recommended)"
        )
        help_text.setStyleSheet("color: #888; font-size: 11px;")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self._cancel_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._save_settings)
        button_layout.addWidget(self._save_btn)

        layout.addLayout(button_layout)

    def _toggle_soniox_visibility(self, checked: bool):
        """Toggle Soniox key visibility."""
        if checked:
            self._soniox_key.setEchoMode(QLineEdit.EchoMode.Normal)
            self._soniox_show_btn.setText("Hide")
        else:
            self._soniox_key.setEchoMode(QLineEdit.EchoMode.Password)
            self._soniox_show_btn.setText("Show")

    def _toggle_gemini_visibility(self, checked: bool):
        """Toggle Gemini key visibility."""
        if checked:
            self._gemini_key.setEchoMode(QLineEdit.EchoMode.Normal)
            self._gemini_show_btn.setText("Hide")
        else:
            self._gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
            self._gemini_show_btn.setText("Show")

    def _load_settings(self):
        """Load settings from file and environment."""
        # First check environment variables
        soniox_key = os.getenv("SONIOX_API_KEY", "")
        gemini_key = os.getenv("GEMINI_API_KEY", "")

        # Then check settings file (overrides env vars if present)
        if self.SETTINGS_FILE.exists():
            try:
                content = self.SETTINGS_FILE.read_text()
                for line in content.strip().split("\n"):
                    if "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip()
                        if key == "SONIOX_API_KEY" and value:
                            soniox_key = value
                        elif key == "GEMINI_API_KEY" and value:
                            gemini_key = value
            except Exception:
                pass

        self._soniox_key.setText(soniox_key)
        self._gemini_key.setText(gemini_key)

    def _save_settings(self):
        """Save settings to file and update environment."""
        soniox_key = self._soniox_key.text().strip()
        gemini_key = self._gemini_key.text().strip()

        # Save to file
        try:
            content = f"SONIOX_API_KEY={soniox_key}\nGEMINI_API_KEY={gemini_key}\n"
            self.SETTINGS_FILE.write_text(content)
            # Set restrictive permissions (owner read/write only)
            self.SETTINGS_FILE.chmod(0o600)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save settings: {e}")
            return

        # Update environment variables for current session
        if soniox_key:
            os.environ["SONIOX_API_KEY"] = soniox_key
        elif "SONIOX_API_KEY" in os.environ:
            del os.environ["SONIOX_API_KEY"]

        if gemini_key:
            os.environ["GEMINI_API_KEY"] = gemini_key
        elif "GEMINI_API_KEY" in os.environ:
            del os.environ["GEMINI_API_KEY"]

        self.settings_changed.emit()
        self.accept()

    def _apply_dark_theme(self):
        """Apply dark theme styling."""
        self.setStyleSheet("""
            QDialog {
                background-color: #2d2d2d;
                color: #fff;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 4px;
                margin-top: 12px;
                padding-top: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #fff;
            }
            QLabel {
                color: #fff;
            }
            QLineEdit {
                background-color: #3d3d3d;
                color: #fff;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 8px;
            }
            QLineEdit:focus {
                border-color: #2196f3;
            }
            QPushButton {
                background-color: #3d3d3d;
                color: #fff;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
            }
            QPushButton:pressed {
                background-color: #2d2d2d;
            }
            QPushButton:checked {
                background-color: #2196f3;
                border-color: #2196f3;
            }
        """)

    @staticmethod
    def get_soniox_key() -> str | None:
        """Get the Soniox API key from environment or settings file."""
        # Check environment first
        key = os.getenv("SONIOX_API_KEY")
        if key:
            return key

        # Check settings file
        settings_file = SettingsDialog.SETTINGS_FILE
        if settings_file.exists():
            try:
                content = settings_file.read_text()
                for line in content.strip().split("\n"):
                    if line.startswith("SONIOX_API_KEY="):
                        value = line.split("=", 1)[1].strip()
                        if value:
                            os.environ["SONIOX_API_KEY"] = value
                            return value
            except Exception:
                pass

        return None

    @staticmethod
    def get_gemini_key() -> str | None:
        """Get the Gemini API key from environment or settings file."""
        # Check environment first
        key = os.getenv("GEMINI_API_KEY")
        if key:
            return key

        # Check settings file
        settings_file = SettingsDialog.SETTINGS_FILE
        if settings_file.exists():
            try:
                content = settings_file.read_text()
                for line in content.strip().split("\n"):
                    if line.startswith("GEMINI_API_KEY="):
                        value = line.split("=", 1)[1].strip()
                        if value:
                            os.environ["GEMINI_API_KEY"] = value
                            return value
            except Exception:
                pass

        return None

    @staticmethod
    def load_settings_to_env():
        """Load settings from file into environment variables."""
        SettingsDialog.get_soniox_key()
        SettingsDialog.get_gemini_key()
