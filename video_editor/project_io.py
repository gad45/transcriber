"""Headless project helpers for Codex-facing workflows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analyzer import SegmentAction, TimeRange
from .transcriber import Segment, Token


DEFAULT_CAPTION_SETTINGS = {
    "font_size": 24,
    "font_family": "Arial",
    "enabled": True,
    "pos_x": 0.5,
    "pos_y": 0.92,
    "box_width": 0.6,
    "box_height": 0.07,
    "show_background": True,
    "text_color": "white",
    "font_weight": "bold",
}


@dataclass
class ProjectData:
    """Parsed data from a `.vedproj` file without importing the GUI package."""

    path: Path
    video_path: Path
    video_duration: float
    segments: list[Segment]
    tokens: list[Token]
    analyzed: list[dict[str, Any]]
    original_keep_ranges: list[TimeRange]
    text_edits: dict[int, str]
    keep_overrides: dict[int, bool]
    highlight_regions: list[dict[str, Any]]
    caption_settings: dict[str, Any]
    raw: dict[str, Any]


def format_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm."""
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def parse_time(value: str | float | int) -> float:
    """Parse seconds, MM:SS(.mmm), or HH:MM:SS(.mmm)."""
    if isinstance(value, int | float):
        return float(value)

    text = str(value).strip()
    if not text:
        raise ValueError("time value cannot be empty")

    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)

    parts = text.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"invalid time value: {value}")

    try:
        if len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            return minutes * 60 + seconds

        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds
    except ValueError as exc:
        raise ValueError(f"invalid time value: {value}") from exc


def merge_ranges(ranges: list[TimeRange], gap_tolerance: float = 0.05) -> list[TimeRange]:
    """Merge overlapping or nearly adjacent ranges."""
    if not ranges:
        return []

    sorted_ranges = sorted(ranges, key=lambda item: item.start)
    merged = [sorted_ranges[0]]

    for current in sorted_ranges[1:]:
        previous = merged[-1]
        if current.start <= previous.end + gap_tolerance:
            merged[-1] = TimeRange(previous.start, max(previous.end, current.end))
        else:
            merged.append(current)

    return merged


def segment_to_dict(segment: Segment) -> dict[str, Any]:
    """Serialize a transcript segment using the GUI project schema."""
    return {
        "start": segment.start,
        "end": segment.end,
        "text": segment.text,
        "confidence": segment.confidence,
    }


def token_to_dict(token: Token) -> dict[str, Any]:
    """Serialize a transcript token using the GUI project schema."""
    return {
        "text": token.text,
        "start": token.start,
        "end": token.end,
    }


def build_project_payload(
    *,
    video_path: Path,
    video_duration: float,
    segments: list[Segment],
    tokens: list[Token],
    analyzed: list[dict[str, Any]],
    keep_ranges: list[TimeRange],
    highlight_regions: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a `.vedproj` payload that remains editable in the GUI."""
    return {
        "version": "1.2",
        "video_path": str(Path(video_path).expanduser().resolve()),
        "video_duration": video_duration,
        "segments": [segment_to_dict(segment) for segment in segments],
        "tokens": [token_to_dict(token) for token in tokens],
        "analyzed": analyzed,
        "original_keep_ranges": [
            {"start": range_.start, "end": range_.end}
            for range_ in keep_ranges
        ],
        "text_edits": {},
        "keep_overrides": {},
        "highlight_regions": highlight_regions or [],
        "crop_config": None,
        "segment_crop_overrides": None,
        "caption_settings": DEFAULT_CAPTION_SETTINGS.copy(),
        "codex_analysis": metadata or {},
    }


def write_project(path: Path, payload: dict[str, Any]) -> Path:
    """Write a `.vedproj` JSON file."""
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return path


def load_project(path: Path) -> ProjectData:
    """Load a `.vedproj` file without importing PySide GUI modules."""
    path = Path(path).expanduser().resolve()
    with path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    segments = [
        Segment(
            start=float(item["start"]),
            end=float(item["end"]),
            text=str(item["text"]),
            confidence=float(item.get("confidence", 1.0)),
        )
        for item in raw.get("segments", [])
    ]
    tokens = [
        Token(
            text=str(item["text"]),
            start=float(item["start"]),
            end=float(item["end"]),
        )
        for item in raw.get("tokens", [])
    ]

    return ProjectData(
        path=path,
        video_path=Path(raw["video_path"]).expanduser(),
        video_duration=float(raw.get("video_duration", 0.0)),
        segments=segments,
        tokens=tokens,
        analyzed=list(raw.get("analyzed", [])),
        original_keep_ranges=[
            TimeRange(float(item["start"]), float(item["end"]))
            for item in raw.get("original_keep_ranges", [])
            if float(item.get("end", 0.0)) > float(item.get("start", 0.0))
        ],
        text_edits={int(key): value for key, value in raw.get("text_edits", {}).items()},
        keep_overrides={int(key): bool(value) for key, value in raw.get("keep_overrides", {}).items()},
        highlight_regions=list(raw.get("highlight_regions", [])),
        caption_settings={**DEFAULT_CAPTION_SETTINGS, **raw.get("caption_settings", {})},
        raw=raw,
    )


def is_segment_kept(project: ProjectData, index: int) -> bool:
    """Return the effective keep/cut decision for a segment."""
    if index in project.keep_overrides:
        return project.keep_overrides[index]

    if index < len(project.analyzed):
        return project.analyzed[index].get("action", SegmentAction.KEEP.value) == SegmentAction.KEEP.value

    return True


def get_segment_text(project: ProjectData, index: int) -> str:
    """Return edited text if present, otherwise original segment text."""
    if index in project.text_edits:
        return project.text_edits[index]
    if 0 <= index < len(project.segments):
        return project.segments[index].text
    return ""


def get_final_keep_ranges(
    project: ProjectData,
    *,
    start_buffer: float,
    end_buffer: float,
) -> list[TimeRange]:
    """Compute export keep ranges from a project, including highlight regions."""
    ranges: list[TimeRange] = []

    if project.original_keep_ranges and not project.keep_overrides:
        ranges.extend(project.original_keep_ranges)
    else:
        for index, segment in enumerate(project.segments):
            if is_segment_kept(project, index):
                ranges.append(TimeRange(
                    start=max(0.0, segment.start - start_buffer),
                    end=min(project.video_duration, segment.end + end_buffer),
                ))

    for highlight in project.highlight_regions:
        start = float(highlight["start"])
        end = float(highlight["end"])
        if end > start:
            ranges.append(TimeRange(
                start=max(0.0, start),
                end=min(project.video_duration, end),
            ))

    return merge_ranges(ranges)


def get_final_tokens(project: ProjectData) -> list[Token]:
    """Compute caption tokens for kept transcript segments."""
    if not project.tokens:
        return []

    result: list[Token] = []
    for index, segment in enumerate(project.segments):
        if not is_segment_kept(project, index):
            continue

        segment_tokens = [
            token for token in project.tokens
            if segment.start <= token.start < segment.end
        ]

        if index in project.text_edits and segment_tokens:
            words = project.text_edits[index].split()
            if not words:
                continue

            start_time = segment_tokens[0].start
            end_time = segment_tokens[-1].end
            duration = max(0.001, end_time - start_time)
            for word_index, word in enumerate(words):
                word_start = start_time + (word_index / len(words)) * duration
                word_end = start_time + ((word_index + 1) / len(words)) * duration
                text = f" {word}" if word_index else word
                result.append(Token(text=text, start=word_start, end=word_end))
        else:
            result.extend(segment_tokens)

    return result


def add_highlight_range(
    project_path: Path,
    *,
    start: float,
    end: float,
    label: str = "",
) -> dict[str, Any]:
    """Append a force-include highlight range to a project file."""
    project = load_project(project_path)
    if end <= start:
        raise ValueError("highlight end must be after start")
    if start < 0:
        raise ValueError("highlight start cannot be negative")
    if project.video_duration and end > project.video_duration:
        raise ValueError("highlight end exceeds project video duration")

    highlight = {
        "start": float(start),
        "end": float(end),
        "label": label,
    }
    project.raw.setdefault("highlight_regions", []).append(highlight)
    write_project(project.path, project.raw)
    return highlight
