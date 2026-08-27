"""Shared rendering pipeline for GUI exports and edited previews."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable

from ..captioner import Captioner
from ..config import Config
from ..cutter import Cutter
from ..export_pipeline import adjust_tokens_for_cuts
from .models import EditSession


ProgressCallback = Callable[[str], None]


def render_edit_session(
    session: EditSession,
    config: Config,
    output_path: Path,
    progress: ProgressCallback | None = None,
) -> Path:
    """Render one immutable edit-session snapshot to a media file."""
    if output_path == session.video_path.resolve():
        raise RuntimeError("Choose a different output path than the source media.")

    def report(message: str) -> None:
        if progress:
            progress(message)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    caption_settings = session.caption_settings
    config.caption_font_size = caption_settings.font_size
    config.caption_font = caption_settings.font_family

    if caption_settings.pos_y < 0.35:
        config.caption_position = "top"
        config.caption_vertical_offset = caption_settings.pos_y * 1080
    elif caption_settings.pos_y < 0.65:
        config.caption_position = "center"
        config.caption_vertical_offset = 60.0
    else:
        config.caption_position = "bottom"
        config.caption_vertical_offset = (1.0 - caption_settings.pos_y) * 1080

    cutter = Cutter(config)
    source_has_video = cutter.input_has_video(session.video_path)
    keep_ranges = session.get_final_keep_ranges(
        config.segment_start_buffer,
        config.segment_end_buffer,
    )
    report("Cutting video..." if source_has_video else "Cutting audio...")

    crop_filter = None
    segment_crop_filters = None
    if source_has_video and session.crop_config and not session.crop_config.is_default:
        video_w, video_h = cutter.get_video_dimensions(session.video_path)
        crop_filter = session.crop_config.to_ffmpeg_filter(video_w, video_h)

    if source_has_video and session.segment_crop_overrides:
        video_w, video_h = cutter.get_video_dimensions(session.video_path)
        segment_crop_filters = {
            index: crop.to_ffmpeg_filter(video_w, video_h)
            for index, crop in session.segment_crop_overrides.items()
            if not crop.is_default
        } or None

    temp_suffix = ".mp4" if source_has_video else ".m4a"
    with tempfile.NamedTemporaryFile(
        prefix="video_editor_render_",
        suffix=temp_suffix,
        delete=False,
    ) as temp_file:
        temp_cut = Path(temp_file.name)
    temp_cut.unlink(missing_ok=True)

    try:
        cutter.cut_video(
            session.video_path,
            keep_ranges,
            temp_cut,
            crop_filter=crop_filter,
            segment_crop_filters=segment_crop_filters,
        )

        tokens = session.get_final_tokens()
        if source_has_video and tokens and session.caption_settings.enabled:
            report("Adding captions...")
            adjusted_tokens = adjust_tokens_for_cuts(
                tokens,
                keep_ranges,
                Cutter.SEGMENT_GAP,
            )
            Captioner(config).burn_streaming_captions(
                temp_cut,
                adjusted_tokens,
                output_path,
                max_words=config.max_caption_words,
                caption_settings=session.caption_settings.to_dict(),
            )
        else:
            report("Finalizing export...")
            shutil.move(str(temp_cut), str(output_path))

        return output_path
    finally:
        temp_cut.unlink(missing_ok=True)
