"""Regression coverage for caption burn-in exports."""

import json
import subprocess
from pathlib import Path

from video_editor.captioner import Captioner
from video_editor.config import Config
from video_editor.runtime_paths import ffmpeg_executable, ffprobe_executable
from video_editor.transcriber import Token


def _stream_types(media_path: Path) -> list[str]:
    result = subprocess.run(
        [
            ffprobe_executable(),
            "-v", "error",
            "-show_entries", "stream=codec_type",
            "-of", "json",
            str(media_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [stream["codec_type"] for stream in json.loads(result.stdout)["streams"]]


def _pixel_rgb(media_path: Path, seconds: float, x: int, y: int) -> tuple[int, int, int]:
    result = subprocess.run(
        [
            ffmpeg_executable(),
            "-v", "error",
            "-ss", str(seconds),
            "-i", str(media_path),
            # Use an even crop so yuv420p can be decoded by every bundled
            # FFmpeg build; the first RGB triplet is the requested pixel.
            "-vf", f"crop=2:2:{x}:{y}",
            "-frames:v", "1",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    return tuple(result.stdout[:3])


def test_caption_export_burns_preview_overlay_with_gui_settings(
    tmp_path: Path,
    monkeypatch,
):
    """GUI exports must burn preview-matched pixels, not soft subtitles."""
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "burned.mp4"
    subprocess.run(
        [
            ffmpeg_executable(),
            "-y",
            "-f", "lavfi",
            "-i", "color=c=blue:s=320x180:r=30:d=2",
            "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:v", "libx264",
            "-c:a", "aac",
            str(source_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    captioner = Captioner(Config(use_hardware_encoding=False))

    def unexpected_filter_probe(_name: str) -> bool:
        raise AssertionError("GUI caption exports must use the preview renderer")

    monkeypatch.setattr(captioner, "_check_ffmpeg_filter", unexpected_filter_probe)
    captioner.burn_streaming_captions(
        source_path,
        [
            Token(text="Hello", start=0.2, end=0.4),
            Token(text="world", start=0.5, end=0.8),
        ],
        output_path,
        max_words=15,
        caption_settings={
            "font_size": 20,
            "font_family": "Arial",
            "font_weight": "bold",
            "text_color": "white",
            "show_background": True,
            "pos_x": 0.75,
            "pos_y": 0.9,
            "box_width": 0.4,
            "box_height": 0.2,
        },
    )

    # The old behavior emitted a mov_text subtitle stream with the bundled
    # FFmpeg build. Captions must be pixels in the video stream instead.
    assert _stream_types(output_path) == ["video", "audio"]

    # The box top-left is x=176, y=126.  That pixel should be the preview's
    # semi-transparent black background rather than the blue source frame.
    red, green, blue = _pixel_rgb(output_path, 0.6, 178, 128)
    assert red < 40
    assert green < 40
    assert blue < 80
