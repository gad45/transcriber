"""Lightweight visual checks for silent screen-recording ranges."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .transcriber import Segment
from .runtime_paths import ffmpeg_executable

FFMPEG = ffmpeg_executable()


@dataclass
class SilentVisualRange:
    """A no-speech range that may still contain useful screen activity."""

    start: float
    end: float
    motion_score: float
    sampled_frames: int
    reason: str

    @property
    def duration(self) -> float:
        return self.end - self.start


def _frame_hashes(video_path: Path, start: float, end: float, fps: float = 1.0) -> list[str]:
    """Return low-resolution frame hashes for a time range."""
    duration = max(0.0, end - start)
    if duration <= 0:
        return []

    cmd = [
        FFMPEG,
        "-hide_banner",
        "-loglevel", "error",
        "-nostats",
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(video_path),
        "-an",
        "-vf", f"fps={fps},scale=160:-1,format=gray",
        "-f", "framemd5",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return []

    hashes: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if parts:
            hashes.append(parts[-1])
    return hashes


def visual_motion_score(video_path: Path, start: float, end: float) -> tuple[float, int]:
    """
    Estimate visual change inside a range.

    This intentionally uses FFmpeg only, so the CLI does not need image
    dependencies. A score near 0 means sampled frames were mostly identical;
    a score near 1 means nearly every sampled frame changed.
    """
    hashes = _frame_hashes(video_path, start, end)
    if len(hashes) < 2:
        return 0.0, len(hashes)

    changed_pairs = sum(
        1 for previous, current in zip(hashes, hashes[1:])
        if previous != current
    )
    return changed_pairs / (len(hashes) - 1), len(hashes)


def iter_silent_gaps(
    segments: list[Segment],
    video_duration: float,
    min_gap: float,
) -> list[tuple[float, float]]:
    """Return no-speech gaps from the transcript timeline."""
    if not segments:
        return [(0.0, video_duration)] if video_duration >= min_gap else []

    sorted_segments = sorted(segments, key=lambda item: item.start)
    gaps: list[tuple[float, float]] = []

    if sorted_segments[0].start >= min_gap:
        gaps.append((0.0, sorted_segments[0].start))

    for previous, current in zip(sorted_segments, sorted_segments[1:]):
        if current.start - previous.end >= min_gap:
            gaps.append((previous.end, current.start))

    if video_duration - sorted_segments[-1].end >= min_gap:
        gaps.append((sorted_segments[-1].end, video_duration))

    return gaps


def detect_silent_visual_ranges(
    *,
    video_path: Path,
    segments: list[Segment],
    video_duration: float,
    min_gap: float = 2.0,
    motion_threshold: float = 0.2,
) -> list[SilentVisualRange]:
    """Find silent gaps that have enough frame changes to merit review."""
    ranges: list[SilentVisualRange] = []

    for start, end in iter_silent_gaps(segments, video_duration, min_gap):
        score, frame_count = visual_motion_score(video_path, start, end)
        if score >= motion_threshold:
            ranges.append(SilentVisualRange(
                start=start,
                end=end,
                motion_score=score,
                sampled_frames=frame_count,
                reason="No speech detected, but sampled screen frames changed.",
            ))

    return ranges

