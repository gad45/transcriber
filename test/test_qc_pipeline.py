from video_editor.analyzer import SegmentAction, TimeRange
from video_editor.project_io import build_project_payload, load_project, write_project
from video_editor.qc_pipeline import (
    QCIssue,
    _classify_issues,
    _check_boundaries,
    _check_export_transcript,
    _status_from_issues,
    issue_summary,
    normalize_text,
    repair_project_boundaries,
)
from video_editor.transcriber import Segment, Token


def test_normalize_text_preserves_hungarian_letters():
    assert normalize_text("Ő az, ugye? Hát igen!") == "ő az ugye hát igen"


def test_boundary_check_flags_tight_edges(tmp_path):
    project_path = tmp_path / "sample.vedproj"
    payload = build_project_payload(
        video_path=tmp_path / "source.mp4",
        video_duration=4.0,
        segments=[Segment(start=1.0, end=2.0, text="első mondat", confidence=1.0)],
        tokens=[
            Token(text="első", start=1.05, end=1.35),
            Token(text=" mondat", start=1.5, end=1.9),
        ],
        analyzed=[{"action": SegmentAction.KEEP.value, "reason": "kept", "retake_group_id": None}],
        keep_ranges=[TimeRange(1.0, 2.0)],
    )
    write_project(project_path, payload)

    issues = _check_boundaries(load_project(project_path), [TimeRange(1.0, 2.0)])
    assert {issue.type for issue in issues} == {"unsafe_start_boundary", "unsafe_end_boundary"}


def test_boundary_check_downgrades_boundaries_blocked_by_removed_tokens(tmp_path):
    project_path = tmp_path / "sample.vedproj"
    payload = build_project_payload(
        video_path=tmp_path / "source.mp4",
        video_duration=4.0,
        segments=[
            Segment(start=0.8, end=1.0, text="rossz", confidence=1.0),
            Segment(start=1.08, end=1.5, text="jo", confidence=1.0),
            Segment(start=1.46, end=1.8, text="rossz", confidence=1.0),
        ],
        tokens=[
            Token(text="rossz", start=0.84, end=1.02),
            Token(text=" jo", start=1.08, end=1.2),
            Token(text=" rossz", start=1.44, end=1.76),
        ],
        analyzed=[
            {"action": SegmentAction.REMOVE.value, "reason": "bad take", "retake_group_id": 1},
            {"action": SegmentAction.KEEP.value, "reason": "kept", "retake_group_id": None},
            {"action": SegmentAction.REMOVE.value, "reason": "bad take", "retake_group_id": 1},
        ],
        keep_ranges=[TimeRange(1.04, 1.42)],
    )
    write_project(project_path, payload)

    issues = _check_boundaries(load_project(project_path), [TimeRange(1.04, 1.42)])

    assert {issue.type for issue in issues} == {
        "tight_start_boundary_after_removed_token",
        "tight_end_boundary_before_removed_token",
    }
    assert {issue.severity for issue in issues} == {"medium"}


def test_export_transcript_check_detects_missing_kept_segment(tmp_path):
    project_path = tmp_path / "sample.vedproj"
    payload = build_project_payload(
        video_path=tmp_path / "source.mp4",
        video_duration=8.0,
        segments=[
            Segment(start=1.0, end=2.0, text="ez benne marad", confidence=1.0),
            Segment(start=4.0, end=5.0, text="ez ki lett vágva", confidence=1.0),
        ],
        tokens=[],
        analyzed=[
            {"action": SegmentAction.KEEP.value, "reason": "kept", "retake_group_id": None},
            {"action": SegmentAction.REMOVE.value, "reason": "bad take", "retake_group_id": 1},
        ],
        keep_ranges=[TimeRange(0.8, 2.2)],
    )
    write_project(project_path, payload)

    issues = _check_export_transcript(
        project=load_project(project_path),
        actual_text="teljesen mas szoveg hallatszik",
        analysis_report=None,
    )
    assert any(issue.type == "kept_segment_missing_or_changed" for issue in issues)


def test_repair_project_boundaries_trims_removed_crossing_token(tmp_path):
    project_path = tmp_path / "sample.vedproj"
    payload = build_project_payload(
        video_path=tmp_path / "source.mp4",
        video_duration=6.0,
        segments=[
            Segment(start=1.0, end=2.0, text="jo szoveg", confidence=1.0),
            Segment(start=2.0, end=3.0, text="rossz szo", confidence=1.0),
        ],
        tokens=[
            Token(text="jo", start=1.1, end=1.3),
            Token(text=" szoveg", start=1.5, end=1.8),
            Token(text=" rossz", start=1.95, end=2.2),
        ],
        analyzed=[
            {"action": SegmentAction.KEEP.value, "reason": "kept", "retake_group_id": None},
            {"action": SegmentAction.REMOVE.value, "reason": "bad take", "retake_group_id": 1},
        ],
        keep_ranges=[TimeRange(0.9, 2.05)],
    )
    write_project(project_path, payload)

    repaired_path, changed = repair_project_boundaries(project_path)
    project = load_project(repaired_path)

    assert changed == 1
    assert project.original_keep_ranges[0].end < 1.95


def test_repair_project_boundaries_trims_trailing_non_kept_token(tmp_path):
    project_path = tmp_path / "sample.vedproj"
    payload = build_project_payload(
        video_path=tmp_path / "source.mp4",
        video_duration=6.0,
        segments=[
            Segment(start=1.0, end=2.0, text="jo szoveg", confidence=1.0),
            Segment(start=2.6, end=3.0, text="kovetkezo", confidence=1.0),
        ],
        tokens=[
            Token(text="jo", start=1.1, end=1.3),
            Token(text=" szoveg", start=1.5, end=1.8),
            Token(text=" filler", start=2.08, end=2.16),
            Token(text=" kovetkezo", start=2.6, end=2.9),
        ],
        analyzed=[
            {"action": SegmentAction.KEEP.value, "reason": "kept", "retake_group_id": None},
            {"action": SegmentAction.REMOVE.value, "reason": "bad take", "retake_group_id": 1},
        ],
        keep_ranges=[TimeRange(0.9, 2.3)],
    )
    write_project(project_path, payload)

    repaired_path, changed = repair_project_boundaries(project_path)
    project = load_project(repaired_path)

    assert changed == 1
    assert project.original_keep_ranges[0].end < 2.08


def test_repair_project_boundaries_splits_interior_non_kept_token(tmp_path):
    project_path = tmp_path / "sample.vedproj"
    payload = build_project_payload(
        video_path=tmp_path / "source.mp4",
        video_duration=8.0,
        segments=[
            Segment(start=1.0, end=2.0, text="elso jo", confidence=1.0),
            Segment(start=3.0, end=4.0, text="masodik jo", confidence=1.0),
        ],
        tokens=[
            Token(text="elso", start=1.1, end=1.3),
            Token(text=" jo", start=1.5, end=1.8),
            Token(text=" filler", start=2.4, end=2.6),
            Token(text=" masodik", start=3.1, end=3.4),
            Token(text=" jo", start=3.6, end=3.8),
        ],
        analyzed=[
            {"action": SegmentAction.KEEP.value, "reason": "kept", "retake_group_id": None},
            {"action": SegmentAction.KEEP.value, "reason": "kept", "retake_group_id": None},
        ],
        keep_ranges=[TimeRange(0.9, 4.2)],
    )
    write_project(project_path, payload)

    repaired_path, changed = repair_project_boundaries(project_path)
    project = load_project(repaired_path)

    assert changed == 1
    assert len(project.original_keep_ranges) == 2
    assert project.original_keep_ranges[0].end < 2.4
    assert project.original_keep_ranges[1].start > 2.6


def test_qc_classification_separates_accepted_and_false_positive_review_items(tmp_path):
    project_path = tmp_path / "sample.vedproj"
    payload = build_project_payload(
        video_path=tmp_path / "source.mp4",
        video_duration=8.0,
        segments=[Segment(start=1.0, end=2.0, text="nem hirdetnek", confidence=1.0)],
        tokens=[Token(text="nem", start=1.1, end=1.2)],
        analyzed=[{"action": SegmentAction.KEEP.value, "reason": "kept", "retake_group_id": None}],
        keep_ranges=[TimeRange(0.8, 2.2)],
        highlight_regions=[{"start": 4.0, "end": 5.0, "label": "screen"}],
    )
    write_project(project_path, payload)
    project = load_project(project_path)
    issues = _classify_issues(project, [
        QCIssue(
            severity="medium",
            type="silent_visual_context",
            reason="screen moved",
            recommendation="review",
            source_start=4.1,
            source_end=4.9,
        ),
        QCIssue(
            severity="medium",
            type="kept_self_correction_marker",
            reason="contains marker",
            recommendation="review",
            source_start=1.0,
            source_end=2.0,
            evidence={"markers": ["nem"]},
        ),
    ])

    assert [issue.review_status for issue in issues] == [
        "accepted_style",
        "likely_false_positive",
    ]
    assert _status_from_issues(issues) == "pass"
    assert issue_summary(issues)["accepted_style"] == 1
    assert issue_summary(issues)["likely_false_positive"] == 1
