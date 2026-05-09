"""Headless project helpers for Codex-facing workflows."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
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
    inferred_crop = infer_recording_crop_config(video_path)
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
        "crop_config": inferred_crop,
        "recording_crop_cleared": False,
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

    if not raw.get("crop_config") and not raw.get("recording_crop_cleared"):
        inferred_crop = infer_recording_crop_config(Path(raw["video_path"]).expanduser())
        if inferred_crop:
            raw["crop_config"] = inferred_crop

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


def crop_config_from_rect(
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    video_width: int,
    video_height: int,
) -> dict[str, float]:
    """Build a GUI-compatible crop config from a source pixel rectangle."""
    if video_width <= 0 or video_height <= 0:
        raise ValueError("video dimensions must be positive")
    if width <= 0 or height <= 0:
        raise ValueError("crop width and height must be positive")
    if x < 0 or y < 0:
        raise ValueError("crop x/y cannot be negative")
    if x + width > video_width or y + height > video_height:
        raise ValueError("crop rectangle exceeds source video dimensions")

    max_pan_x = video_width - width
    max_pan_y = video_height - height
    pan_x = (2 * x / max_pan_x) - 1 if max_pan_x > 0 else 0.0
    pan_y = (2 * y / max_pan_y) - 1 if max_pan_y > 0 else 0.0

    return {
        "width": width / video_width,
        "height": height / video_height,
        "pan_x": max(-1.0, min(1.0, pan_x)),
        "pan_y": max(-1.0, min(1.0, pan_y)),
    }


def recording_crop_sidecar_candidates(video_path: Path) -> list[Path]:
    """Return possible recorder crop sidecar locations for a video."""
    path = Path(video_path).expanduser()
    candidates: list[Path] = []

    if path.parent.name == "raw":
        candidates.append(path.parent.parent / f"{path.stem}.crop.json")

    candidates.append(path.with_suffix(".crop.json"))

    # Preserve order while avoiding duplicate paths.
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def find_recording_crop_sidecar(video_path: Path) -> Path | None:
    """Find a crop sidecar written by the recorder, if one exists."""
    for candidate in recording_crop_sidecar_candidates(video_path):
        if candidate.exists():
            return candidate
    return None


def _parse_crop_filter(crop_filter: str) -> tuple[int, int, int, int] | None:
    match = re.search(r"(?:^|,)crop=(\d+):(\d+):(\d+):(\d+)(?:,|$)", crop_filter)
    if not match:
        return None
    width, height, x, y = (int(value) for value in match.groups())
    return x, y, width, height


def crop_config_from_recording_sidecar(
    sidecar_path: Path,
    *,
    expected_raw_path: Path | None = None,
) -> dict[str, float] | None:
    """Recover a GUI crop config from a recorder `.crop.json` sidecar."""
    try:
        payload = json.loads(Path(sidecar_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if expected_raw_path is not None:
        raw_path = payload.get("raw_path")
        if isinstance(raw_path, str) and raw_path.strip():
            try:
                sidecar_raw_path = Path(raw_path).expanduser().resolve()
                expected_path = Path(expected_raw_path).expanduser().resolve()
                if sidecar_raw_path != expected_path:
                    return None
            except OSError:
                return None
        elif Path(expected_raw_path).expanduser().parent.name != "raw":
            return None

    screen_size: tuple[int, int] | None = None
    crop_rect: tuple[int, int, int, int] | None = None

    for event in payload.get("events", []):
        raw_screen_size = event.get("screen_size")
        if (
            isinstance(raw_screen_size, list)
            and len(raw_screen_size) == 2
            and all(isinstance(value, (int, float)) for value in raw_screen_size)
        ):
            screen_size = (int(raw_screen_size[0]), int(raw_screen_size[1]))

        raw_filter = event.get("crop_filter")
        if isinstance(raw_filter, str):
            parsed = _parse_crop_filter(raw_filter)
            if parsed:
                crop_rect = parsed

    if not crop_rect or not screen_size:
        return None

    x, y, width, height = crop_rect
    video_width, video_height = screen_size
    try:
        crop = crop_config_from_rect(
            x=x,
            y=y,
            width=width,
            height=height,
            video_width=video_width,
            video_height=video_height,
        )
    except ValueError:
        return None

    if crop["width"] == 1.0 and crop["height"] == 1.0:
        return None
    return crop


def infer_recording_crop_config(video_path: Path) -> dict[str, float] | None:
    """Infer a crop config from recorder metadata next to a raw recording."""
    sidecar = find_recording_crop_sidecar(video_path)
    if sidecar is None:
        return None
    return crop_config_from_recording_sidecar(sidecar, expected_raw_path=video_path)


def set_project_crop(
    project_path: Path,
    *,
    crop_config: dict[str, Any] | None,
    output_path: Path | None = None,
) -> Path:
    """Set, clear, or copy a project's global crop config."""
    project = load_project(project_path)
    output = Path(output_path).expanduser().resolve() if output_path else project.path
    if crop_config is None:
        project.raw["crop_config"] = None
        project.raw["recording_crop_cleared"] = True
    else:
        project.raw["crop_config"] = {
            "width": float(crop_config.get("width", 1.0)),
            "height": float(crop_config.get("height", 1.0)),
            "pan_x": float(crop_config.get("pan_x", 0.0)),
            "pan_y": float(crop_config.get("pan_y", 0.0)),
        }
        project.raw["recording_crop_cleared"] = False
    write_project(output, project.raw)
    return output


def backup_project_file(project_path: Path, *, suffix: str | None = None) -> Path:
    """Copy a project to a timestamped backup beside the original."""
    source = Path(project_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"project not found: {source}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    label = suffix or f"backup_{timestamp}"
    backup = source.with_name(f"{source.stem}_{label}{source.suffix}")
    counter = 2
    while backup.exists():
        backup = source.with_name(f"{source.stem}_{label}_{counter}{source.suffix}")
        counter += 1

    shutil.copy2(source, backup)
    return backup


def _dedupe_highlights(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge highlight arrays without duplicating identical ranges."""
    highlights: list[dict[str, Any]] = []
    seen: set[tuple[float, float, str]] = set()

    for group in groups:
        for highlight in group:
            try:
                start = float(highlight["start"])
                end = float(highlight["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start:
                continue

            label = str(highlight.get("label", ""))
            key = (round(start, 3), round(end, 3), label)
            if key in seen:
                continue
            seen.add(key)
            highlights.append({"start": start, "end": end, "label": label})

    return highlights


def merge_project_with_analysis(
    manual_project_path: Path,
    analysis_project_path: Path,
    output_path: Path,
) -> Path:
    """
    Combine a manually prepared project with a transcript/analyzed project.

    The analysis project supplies speech segments, tokens, keep decisions, and
    keep ranges. The manual project supplies presentation/editing settings such
    as crop, captions, highlights, and compatible text/keep overrides.
    """
    manual = load_project(manual_project_path)
    analysis = load_project(analysis_project_path)
    if not analysis.segments:
        raise ValueError("analysis project does not contain transcript segments")

    merged = dict(analysis.raw)
    merged["version"] = str(analysis.raw.get("version", "1.2"))

    for key in ("crop_config", "recording_crop_cleared", "segment_crop_overrides", "caption_settings"):
        if key in manual.raw:
            merged[key] = manual.raw.get(key)

    merged["highlight_regions"] = _dedupe_highlights(
        list(analysis.raw.get("highlight_regions", [])),
        list(manual.raw.get("highlight_regions", [])),
    )

    compatible_segments = len(manual.segments) == len(analysis.segments)
    for key in ("text_edits", "keep_overrides"):
        manual_value = manual.raw.get(key)
        if compatible_segments and manual_value:
            merged[key] = manual_value
        else:
            merged.setdefault(key, {})

    analysis_meta = dict(analysis.raw.get("codex_analysis", {}))
    manual_meta = dict(manual.raw.get("codex_analysis", {}))
    merged["codex_analysis"] = {
        **analysis_meta,
        "manual_project": str(manual.path),
        "analysis_project": str(analysis.path),
        "manual_project_metadata": manual_meta,
        "merge_strategy": {
            "analysis_fields": ["segments", "tokens", "analyzed", "original_keep_ranges"],
            "manual_fields": ["crop_config", "segment_crop_overrides", "caption_settings", "highlight_regions"],
            "manual_overrides_preserved": compatible_segments,
        },
    }

    return write_project(output_path, merged)
