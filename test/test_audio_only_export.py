import json
import subprocess
from pathlib import Path

from video_editor.analyzer import TimeRange
from video_editor.config import Config
from video_editor.cutter import Cutter
from video_editor.export_pipeline import export_project
from video_editor.gui.models import EditSession
from video_editor.gui.rendering import render_edit_session
from video_editor.project_io import build_project_payload, load_project, write_project
from video_editor.runtime_paths import ffmpeg_executable, ffprobe_executable


def _probe_stream_types(media_path: Path) -> list[str]:
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


def _probe_audio_duration(media_path: Path) -> float:
    result = subprocess.run(
        [
            ffprobe_executable(),
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def test_audio_only_cut_exports_m4a_without_a_video_stream(tmp_path: Path):
    source_path = tmp_path / "source.m4a"
    output_path = tmp_path / "edited.m4a"
    subprocess.run(
        [
            ffmpeg_executable(),
            "-y",
            "-f", "lavfi",
            "-i", "sine=frequency=1000:sample_rate=48000:duration=3",
            "-c:a", "aac",
            "-b:a", "256k",
            str(source_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    cutter = Cutter(Config(temp_dir=temp_dir))
    result_path = cutter.cut_video(
        source_path,
        [TimeRange(0.0, 0.75), TimeRange(1.25, 2.5)],
        output_path,
    )

    assert result_path == output_path
    assert _probe_stream_types(output_path) == ["audio"]
    assert abs(cutter.get_video_duration(output_path) - 2.0) < 0.03


def test_audio_only_project_export_skips_video_only_caption_processing(tmp_path: Path, monkeypatch):
    source_path = tmp_path / "source.m4a"
    project_path = tmp_path / "source.vedproj"
    output_path = tmp_path / "edited.m4a"
    subprocess.run(
        [
            ffmpeg_executable(),
            "-y",
            "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:a", "aac",
            str(source_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    write_project(
        project_path,
        build_project_payload(
            video_path=source_path,
            video_duration=2.0,
            segments=[],
            tokens=[],
            analyzed=[],
            keep_ranges=[TimeRange(0.0, 1.5)],
        ),
    )

    def fail_if_captioner_is_created(*args, **kwargs):
        raise AssertionError("Audio-only export must not invoke the captioner")

    monkeypatch.setattr("video_editor.export_pipeline.Captioner", fail_if_captioner_is_created)
    temp_dir = tmp_path / "project_temp"
    temp_dir.mkdir()
    result_path = export_project(
        load_project(project_path),
        output_path,
        config=Config(temp_dir=temp_dir),
    )

    assert result_path == output_path
    assert _probe_stream_types(output_path) == ["audio"]


def test_gui_render_uses_the_same_single_pass_audio_timeline(tmp_path: Path):
    source_path = tmp_path / "source.m4a"
    output_path = tmp_path / "preview.m4a"
    subprocess.run(
        [
            ffmpeg_executable(),
            "-y",
            "-f", "lavfi",
            "-i", "sine=frequency=600:sample_rate=48000:duration=3",
            "-c:a", "aac",
            str(source_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    session = EditSession(
        video_path=source_path,
        video_duration=3.0,
        original_keep_ranges=[TimeRange(0.0, 0.75), TimeRange(1.25, 2.5)],
    )
    messages = []

    result_path = render_edit_session(
        session,
        Config(temp_dir=tmp_path / "temp"),
        output_path,
        messages.append,
    )

    assert result_path == output_path
    assert abs(Cutter(Config()).get_video_duration(output_path) - 2.0) < 0.03
    assert messages == ["Cutting audio...", "Finalizing export..."]


def test_video_cut_uses_single_pass_audio_with_exact_transition_gaps(tmp_path: Path):
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "edited.mp4"
    subprocess.run(
        [
            ffmpeg_executable(),
            "-y",
            "-f", "lavfi",
            "-i", "color=c=blue:s=320x180:r=30:d=3",
            "-f", "lavfi",
            "-i", "sine=frequency=800:sample_rate=48000:duration=3",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            str(source_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    Cutter(
        Config(temp_dir=temp_dir, use_hardware_encoding=False)
    ).cut_video(
        source_path,
        [TimeRange(0.0, 0.75), TimeRange(1.25, 2.5)],
        output_path,
    )

    expected_duration = 2.0 + Cutter.SEGMENT_GAP
    assert _probe_stream_types(output_path) == ["video", "audio"]
    assert abs(_probe_audio_duration(output_path) - expected_duration) < 0.03
