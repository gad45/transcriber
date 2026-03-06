"""Best-effort macOS permission helpers for screen capture."""

from __future__ import annotations

import ctypes
import sys


def is_macos() -> bool:
    """Return True when running on macOS."""
    return sys.platform == "darwin"


def _load_core_graphics():
    """Load CoreGraphics for screen capture permission checks."""
    if not is_macos():
        return None

    try:
        return ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    except OSError:
        return None


def has_screen_capture_access() -> bool:
    """Check whether macOS screen capture access is already granted."""
    core_graphics = _load_core_graphics()
    if core_graphics is None:
        return True

    try:
        fn = core_graphics.CGPreflightScreenCaptureAccess
        fn.argtypes = []
        fn.restype = ctypes.c_bool
        return bool(fn())
    except AttributeError:
        return True


def request_screen_capture_access() -> bool:
    """Request macOS screen capture access."""
    if has_screen_capture_access():
        return True

    core_graphics = _load_core_graphics()
    if core_graphics is None:
        return True

    try:
        fn = core_graphics.CGRequestScreenCaptureAccess
        fn.argtypes = []
        fn.restype = ctypes.c_bool
        return bool(fn())
    except AttributeError:
        return True
