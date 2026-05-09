from pathlib import Path

from video_editor.analyzer import SegmentAction, TimeRange
from video_editor.project_io import (
    add_highlight_range,
    backup_project_file,
    build_project_payload,
    crop_config_from_rect,
    get_final_keep_ranges,
    get_final_tokens,
    load_project,
    merge_project_with_analysis,
    parse_time,
    set_project_crop,
    write_project,
)
from video_editor.transcriber import Segment, Token


def test_parse_time_accepts_common_formats():
    assert parse_time("12.5") == 12.5
    assert parse_time("01:02.500") == 62.5
    assert parse_time("01:02:03.250") == 3723.25


def test_project_keeps_original_source_and_appends_highlights(tmp_path: Path):
    source = tmp_path / "recording.mp4"
    project_path = tmp_path / "recording.vedproj"
    payload = build_project_payload(
        video_path=source,
        video_duration=20.0,
        segments=[
            Segment(start=1.0, end=3.0, text="first", confidence=1.0),
            Segment(start=10.0, end=12.0, text="second", confidence=1.0),
        ],
        tokens=[
            Token(text="first", start=1.1, end=2.8),
            Token(text="second", start=10.2, end=11.7),
        ],
        analyzed=[
            {"action": SegmentAction.KEEP.value, "reason": "kept", "retake_group_id": None},
            {"action": SegmentAction.REMOVE.value, "reason": "non_selected_retake", "retake_group_id": 1},
        ],
        keep_ranges=[TimeRange(0.8, 3.3)],
    )
    write_project(project_path, payload)

    add_highlight_range(project_path, start=5.0, end=8.0, label="screen demo")
    project = load_project(project_path)

    assert project.video_path == source.resolve()
    assert project.highlight_regions == [
        {"start": 5.0, "end": 8.0, "label": "screen demo"}
    ]

    ranges = get_final_keep_ranges(project, start_buffer=0.1, end_buffer=0.15)
    assert ranges == [TimeRange(0.8, 3.3), TimeRange(5.0, 8.0)]


def test_final_tokens_exclude_cut_segments(tmp_path: Path):
    project_path = tmp_path / "recording.vedproj"
    payload = build_project_payload(
        video_path=tmp_path / "recording.mp4",
        video_duration=10.0,
        segments=[
            Segment(start=1.0, end=2.0, text="keep", confidence=1.0),
            Segment(start=4.0, end=5.0, text="cut", confidence=1.0),
        ],
        tokens=[
            Token(text="keep", start=1.1, end=1.8),
            Token(text="cut", start=4.1, end=4.8),
        ],
        analyzed=[
            {"action": SegmentAction.KEEP.value, "reason": "kept", "retake_group_id": None},
            {"action": SegmentAction.REMOVE.value, "reason": "bad take", "retake_group_id": 1},
        ],
        keep_ranges=[TimeRange(0.9, 2.2)],
    )
    write_project(project_path, payload)

    tokens = get_final_tokens(load_project(project_path))
    assert [token.text for token in tokens] == ["keep"]


def test_crop_config_from_rect_round_trips_to_project(tmp_path: Path):
    project_path = tmp_path / "recording.vedproj"
    payload = build_project_payload(
        video_path=tmp_path / "recording.mp4",
        video_duration=10.0,
        segments=[],
        tokens=[],
        analyzed=[],
        keep_ranges=[],
    )
    write_project(project_path, payload)

    crop = crop_config_from_rect(
        x=760,
        y=180,
        width=1920,
        height=1080,
        video_width=3440,
        video_height=1440,
    )
    output = set_project_crop(project_path, crop_config=crop)
    project = load_project(output)

    assert project.raw["crop_config"] == crop
    assert crop["width"] == 1920 / 3440
    assert crop["height"] == 1080 / 1440


def test_merge_project_with_analysis_preserves_manual_presentation_settings(tmp_path: Path):
    manual_path = tmp_path / "manual.vedproj"
    analysis_path = tmp_path / "analysis.vedproj"
    output_path = tmp_path / "merged.vedproj"

    manual_payload = build_project_payload(
        video_path=tmp_path / "raw.mp4",
        video_duration=12.0,
        segments=[],
        tokens=[],
        analyzed=[],
        keep_ranges=[],
    )
    manual_payload["crop_config"] = {
        "width": 0.5,
        "height": 0.75,
        "pan_x": 0.2,
        "pan_y": -0.1,
    }
    manual_payload["caption_settings"]["font_size"] = 32
    manual_payload["highlight_regions"] = [
        {"start": 4.0, "end": 5.0, "label": "screen action"}
    ]
    write_project(manual_path, manual_payload)

    analysis_payload = build_project_payload(
        video_path=tmp_path / "raw.mp4",
        video_duration=12.0,
        segments=[Segment(start=1.0, end=2.0, text="kept", confidence=1.0)],
        tokens=[Token(text="kept", start=1.1, end=1.8)],
        analyzed=[{"action": SegmentAction.KEEP.value, "reason": "kept", "retake_group_id": None}],
        keep_ranges=[TimeRange(0.8, 2.2)],
    )
    write_project(analysis_path, analysis_payload)

    merge_project_with_analysis(manual_path, analysis_path, output_path)
    merged = load_project(output_path)

    assert [segment.text for segment in merged.segments] == ["kept"]
    assert merged.raw["crop_config"] == manual_payload["crop_config"]
    assert merged.caption_settings["font_size"] == 32
    assert merged.highlight_regions == manual_payload["highlight_regions"]
    assert merged.raw["codex_analysis"]["manual_project"] == str(manual_path.resolve())


def test_backup_project_file_creates_timestamped_copy(tmp_path: Path):
    project_path = tmp_path / "sample.vedproj"
    payload = build_project_payload(
        video_path=tmp_path / "raw.mp4",
        video_duration=1.0,
        segments=[],
        tokens=[],
        analyzed=[],
        keep_ranges=[],
    )
    write_project(project_path, payload)

    backup = backup_project_file(project_path, suffix="backup_test")

    assert backup.exists()
    assert backup != project_path
    assert load_project(backup).raw["video_path"] == str((tmp_path / "raw.mp4").resolve())
