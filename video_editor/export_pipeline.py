"""Headless export helpers for `.vedproj` files."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from .analyzer import TimeRange
from .captioner import Captioner
from .config import Config
from .cutter import Cutter
from .project_io import ProjectData, get_final_keep_ranges, get_final_tokens
from .transcriber import Token


def adjust_tokens_for_cuts(
    tokens: list[Token],
    keep_ranges: list[TimeRange],
    segment_gap: float = Cutter.SEGMENT_GAP,
) -> list[Token]:
    """Adjust original token times onto the cut-video timeline."""
    if not tokens or not keep_ranges:
        return []

    sorted_tokens = sorted(tokens, key=lambda token: token.start)
    sorted_ranges = sorted(keep_ranges, key=lambda range_: range_.start)

    offsets: list[float] = []
    cumulative = 0.0
    for index, range_ in enumerate(sorted_ranges):
        offsets.append(cumulative)
        cumulative += range_.duration
        if index < len(sorted_ranges) - 1:
            cumulative += segment_gap

    adjusted: list[Token] = []
    range_index = 0

    for token in sorted_tokens:
        while range_index < len(sorted_ranges) and sorted_ranges[range_index].end <= token.start:
            range_index += 1

        if range_index >= len(sorted_ranges):
            break

        range_ = sorted_ranges[range_index]
        if range_.start <= token.start < range_.end:
            offset = offsets[range_index]
            adjusted.append(Token(
                text=token.text,
                start=offset + (token.start - range_.start),
                end=min(
                    offset + (token.end - range_.start),
                    offset + range_.duration,
                ),
            ))

    return adjusted


def _crop_filter_from_project(project: ProjectData, cutter: Cutter) -> str | None:
    """Build the global crop filter stored by the GUI, if present."""
    crop = project.raw.get("crop_config")
    if not crop:
        return None

    width_fraction = float(crop.get("width", 1.0))
    height_fraction = float(crop.get("height", 1.0))
    pan_x = float(crop.get("pan_x", 0.0))
    pan_y = float(crop.get("pan_y", 0.0))

    if width_fraction == 1.0 and height_fraction == 1.0 and pan_x == 0.0 and pan_y == 0.0:
        return None

    video_width, video_height = cutter.get_video_dimensions(project.video_path)
    crop_width = int(width_fraction * video_width)
    crop_height = int(height_fraction * video_height)

    max_pan_x = video_width - crop_width
    max_pan_y = video_height - crop_height
    crop_x = int((max_pan_x / 2) * (1 + pan_x)) if max_pan_x > 0 else 0
    crop_y = int((max_pan_y / 2) * (1 + pan_y)) if max_pan_y > 0 else 0

    crop_x = max(0, min(crop_x, video_width - crop_width))
    crop_y = max(0, min(crop_y, video_height - crop_height))
    return f"crop={crop_width}:{crop_height}:{crop_x}:{crop_y}"


def _apply_caption_settings(config: Config, caption_settings: dict[str, Any]) -> None:
    """Copy project caption settings onto export config."""
    config.caption_font_size = int(caption_settings.get("font_size", config.caption_font_size))
    config.caption_font = str(caption_settings.get("font_family", config.caption_font))

    pos_y = float(caption_settings.get("pos_y", 0.92))
    if pos_y < 0.35:
        config.caption_position = "top"
        config.caption_vertical_offset = pos_y * 1080
    elif pos_y < 0.65:
        config.caption_position = "center"
        config.caption_vertical_offset = 60.0
    else:
        config.caption_position = "bottom"
        config.caption_vertical_offset = (1.0 - pos_y) * 1080


def export_project(
    project: ProjectData,
    output_path: Path,
    *,
    config: Config | None = None,
    no_captions: bool = False,
) -> Path:
    """Export a project by cutting from its original source video."""
    output_path = Path(output_path).expanduser().resolve()
    source_path = project.video_path.expanduser().resolve()
    if output_path == source_path:
        raise RuntimeError("Choose a different output path than the source recording.")

    config = config or Config(temp_dir=Path(tempfile.gettempdir()) / "video_editor")
    if config.temp_dir:
        config.temp_dir.mkdir(parents=True, exist_ok=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _apply_caption_settings(config, project.caption_settings)

    cutter = Cutter(config)
    captioner = Captioner(config)
    keep_ranges = get_final_keep_ranges(
        project,
        start_buffer=config.segment_start_buffer,
        end_buffer=config.segment_end_buffer,
    )
    crop_filter = _crop_filter_from_project(project, cutter)

    if no_captions or not project.caption_settings.get("enabled", True):
        return cutter.cut_video(source_path, keep_ranges, output_path, crop_filter=crop_filter)

    with tempfile.NamedTemporaryFile(prefix="video_editor_codex_", suffix=".mp4", delete=False) as temp_file:
        temp_cut = Path(temp_file.name)
    temp_cut.unlink(missing_ok=True)

    try:
        cutter.cut_video(source_path, keep_ranges, temp_cut, crop_filter=crop_filter)
        tokens = get_final_tokens(project)
        adjusted_tokens = adjust_tokens_for_cuts(tokens, keep_ranges, Cutter.SEGMENT_GAP)
        if adjusted_tokens:
            captioner.burn_streaming_captions(
                temp_cut,
                adjusted_tokens,
                output_path,
                max_words=config.max_caption_words,
                caption_settings=project.caption_settings,
            )
        else:
            shutil.move(str(temp_cut), str(output_path))
    finally:
        temp_cut.unlink(missing_ok=True)

    return output_path

