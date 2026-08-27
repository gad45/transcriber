"""File-dialog policy shared by the editor window and its tests."""

from __future__ import annotations

import os
from pathlib import Path


VIDEO_EXTENSIONS = ("*.mp4", "*.mov", "*.avi", "*.mkv")
AUDIO_EXTENSIONS = ("*.m4a", "*.aac", "*.wav", "*.mp3", "*.flac", "*.ogg")

MEDIA_FILE_FILTER = ";;".join(
    (
        f"Media Files ({' '.join(VIDEO_EXTENSIONS + AUDIO_EXTENSIONS)})",
        f"Audio Files ({' '.join(AUDIO_EXTENSIONS)})",
        f"Video Files ({' '.join(VIDEO_EXTENSIONS)})",
        "All Files (*)",
    )
)
PROJECT_FILE_FILTER = "Video Editor Projects (*.vedproj)"


def with_project_extension(path: Path | str) -> Path:
    """Return *path* with the editor's project extension."""
    path = Path(path).expanduser()
    if path.suffix.lower() == ".vedproj":
        return path
    return Path(f"{path}.vedproj")


def suggested_project_path(media_path: Path, fallback_directory: Path) -> Path:
    """Suggest a writable save path beside the media, never at filesystem root."""
    media_path = Path(media_path).expanduser()
    parent = media_path.parent
    filesystem_root = Path(parent.anchor) if parent.anchor else None

    if (
        parent.is_dir()
        and parent != filesystem_root
        and os.access(parent, os.W_OK)
    ):
        destination = parent
    else:
        destination = Path(fallback_directory).expanduser()

    return destination / f"{media_path.stem}.vedproj"
