"""Helpers for resolving bundled resources at runtime."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _iter_resource_roots() -> list[Path]:
    """Return candidate roots that may contain bundled resources."""
    roots: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))

    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        roots.append(executable_dir)
        roots.append(executable_dir.parent / "Frameworks")
        roots.append(executable_dir.parent / "Resources")

    roots.append(Path(__file__).resolve().parent)

    seen: set[str] = set()
    unique_roots: list[Path] = []
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique_roots.append(root)
    return unique_roots


def resolve_bundled_resource(relative_path: str, *, env_var: str | None = None) -> Path | None:
    """Return a bundled resource path when available."""
    if env_var:
        env_value = os.getenv(env_var, "").strip()
        if env_value:
            env_path = Path(env_value).expanduser()
            if env_path.exists():
                return env_path

    for root in _iter_resource_roots():
        candidate = root / relative_path
        if candidate.exists():
            return candidate

    return None


def resolve_bundled_binary(name: str, *, env_var: str | None = None) -> Path | None:
    """Return a bundled binary path when available."""
    return (
        resolve_bundled_resource(f"bin/{name}", env_var=env_var)
        or resolve_bundled_resource(name, env_var=env_var)
    )


def resolve_command(name: str, *, env_var: str | None = None) -> str:
    """Resolve a command from bundled resources or PATH."""
    bundled = resolve_bundled_binary(name, env_var=env_var)
    if bundled is not None:
        return str(bundled)

    discovered = shutil.which(name)
    if discovered:
        return discovered

    return name


def ffmpeg_executable() -> str:
    """Return the FFmpeg executable path."""
    return resolve_command("ffmpeg", env_var="VIDEO_EDITOR_FFMPEG_PATH")


def ffprobe_executable() -> str:
    """Return the FFprobe executable path."""
    return resolve_command("ffprobe", env_var="VIDEO_EDITOR_FFPROBE_PATH")
