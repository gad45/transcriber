"""GUI entry point for the video editor."""

import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # Load .env file automatically

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase

from .gui import MainWindow


def main():
    """Launch the video editor GUI."""
    # Enable high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)

    # Load fonts from user's font directory (ensures newly installed fonts are available)
    user_fonts_dir = Path.home() / "Library" / "Fonts"
    if user_fonts_dir.exists():
        for font_file in user_fonts_dir.glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(font_file))
        for font_file in user_fonts_dir.glob("*.otf"):
            QFontDatabase.addApplicationFont(str(font_file))
    app.setApplicationName("Video Editor")
    app.setOrganizationName("VideoEditorAgent")

    # Check for video path argument
    video_path = None
    if len(sys.argv) > 1:
        video_path = Path(sys.argv[1])
        if not video_path.exists():
            print(f"Error: File not found: {video_path}")
            sys.exit(1)

    # Create and show main window
    window = MainWindow(video_path=video_path)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
