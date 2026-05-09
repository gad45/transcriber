"""Second-pass quality control for AI-edited recordings."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz
from rich.console import Console

from .analyzer import HUNGARIAN_HESITATION_MARKERS, SegmentAction, TimeRange
from .config import Config
from .cutter import Cutter
from .export_pipeline import crop_filter_from_project
from .project_io import (
    ProjectData,
    format_time,
    get_final_keep_ranges,
    get_segment_text,
    is_segment_kept,
    load_project,
    merge_ranges,
    write_project,
)
from .runtime_paths import ffmpeg_executable, ffprobe_executable
from .transcriber import Segment, Token, Transcriber
from .visual_analysis import detect_silent_visual_ranges

console = Console()
FFMPEG = ffmpeg_executable()
FFPROBE = ffprobe_executable()


BOUNDARY_START_MARGIN = 0.18
BOUNDARY_END_MARGIN = 0.25
SEGMENT_MATCH_THRESHOLD = 68
EDGE_PHRASE_THRESHOLD = 62
REMOVED_TEXT_THRESHOLD = 88


@dataclass
class QCIssue:
    """A single second-pass QC finding."""

    severity: str
    type: str
    reason: str
    recommendation: str
    source_start: float | None = None
    source_end: float | None = None
    export_start: float | None = None
    export_end: float | None = None
    segment_indices: list[int] | None = None
    evidence: dict[str, Any] | None = None
    review_status: str = "unresolved_review"


@dataclass
class QCResult:
    """Complete second-pass QC result."""

    status: str
    project_path: Path
    source_video: Path
    export_video: Path
    export_duration: float
    expected_transcript: str
    actual_transcript: str
    expected_actual_similarity: float
    issues: list[QCIssue]

    def to_dict(self) -> dict[str, Any]:
        summary = issue_summary(self.issues)
        return {
            "status": self.status,
            "project_path": str(self.project_path),
            "source_video": str(self.source_video),
            "export_video": str(self.export_video),
            "export_duration": self.export_duration,
            "expected_actual_similarity": self.expected_actual_similarity,
            "summary": summary,
            "issues": [asdict(issue) for issue in self.issues],
            "expected_transcript": self.expected_transcript,
            "actual_transcript": self.actual_transcript,
        }


def issue_summary(issues: list[QCIssue]) -> dict[str, int]:
    """Return severity and review-status counts for a QC result."""
    return {
        "issue_count": len(issues),
        "high": sum(1 for issue in issues if issue.severity == "high"),
        "medium": sum(1 for issue in issues if issue.severity == "medium"),
        "low": sum(1 for issue in issues if issue.severity == "low"),
        "unresolved_review": sum(1 for issue in issues if issue.review_status == "unresolved_review"),
        "accepted_style": sum(1 for issue in issues if issue.review_status == "accepted_style"),
        "likely_false_positive": sum(1 for issue in issues if issue.review_status == "likely_false_positive"),
    }


def normalize_text(text: str) -> str:
    """Normalize transcript text for approximate matching."""
    lowered = text.lower()
    lowered = re.sub(r"[^\w\sáéíóöőúüű]", " ", lowered, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", lowered).strip()


def content_words(text: str) -> list[str]:
    """Return normalized words, dropping empty tokens."""
    normalized = normalize_text(text)
    return [word for word in normalized.split() if word]


def phrase(words: list[str], *, first: bool, size: int = 3) -> str:
    """Return a short edge phrase."""
    if not words:
        return ""
    selected = words[:size] if first else words[-size:]
    return " ".join(selected)


def expected_kept_segments(project: ProjectData) -> list[tuple[int, Segment, str]]:
    """Return kept source segments with edited text applied."""
    kept: list[tuple[int, Segment, str]] = []
    for index, segment in enumerate(project.segments):
        if is_segment_kept(project, index):
            kept.append((index, segment, get_segment_text(project, index)))
    return kept


def expected_removed_segments(project: ProjectData) -> list[tuple[int, Segment, str]]:
    """Return removed source segments with original text."""
    removed: list[tuple[int, Segment, str]] = []
    for index, segment in enumerate(project.segments):
        if not is_segment_kept(project, index):
            removed.append((index, segment, get_segment_text(project, index)))
    return removed


def transcript_text(segments: list[Segment]) -> str:
    """Join transcript segment text."""
    return " ".join(segment.text for segment in segments).strip()


def _load_analysis_report(project: ProjectData, analysis_path: Path | None) -> dict[str, Any] | None:
    """Load the original analysis JSON when available."""
    candidate: Path | None = None
    if analysis_path:
        candidate = Path(analysis_path).expanduser().resolve()
    else:
        metadata = project.raw.get("codex_analysis", {})
        json_report = metadata.get("json_report")
        if json_report:
            candidate = Path(json_report).expanduser()

    if candidate is None or not candidate.exists():
        return None

    with candidate.open("r", encoding="utf-8") as file:
        return json.load(file)


def _export_duration(export_video: Path) -> float:
    cmd = [
        FFPROBE,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(export_video),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for export: {result.stderr}")
    return float(result.stdout.strip())


def _transcribe_export(export_video: Path, config: Config) -> tuple[list[Segment], list[Token]]:
    """Independently transcribe the exported video."""
    console.print("[blue]Transcribing exported video for independent QC...[/blue]")
    return Transcriber(config).transcribe_video(export_video)


def _retake_similarity_exemption(
    analysis_report: dict[str, Any] | None,
    removed_segment_index: int,
) -> bool:
    """Return True when removed text is expected to match a selected retake."""
    if not analysis_report:
        return False

    for group in analysis_report.get("retake_groups", []):
        selected_text = ""
        removed_text = ""
        for take in group.get("takes", []):
            indices = take.get("segment_indices", [])
            if removed_segment_index not in indices:
                continue
            removed_text = take.get("text", "")
            selected_take_number = group.get("selected_take")
            for candidate in group.get("takes", []):
                if candidate.get("take_number") == selected_take_number:
                    selected_text = candidate.get("text", "")
                    break
        if selected_text and removed_text:
            return fuzz.partial_ratio(normalize_text(removed_text), normalize_text(selected_text)) >= 78
    return False


def _check_export_transcript(
    *,
    project: ProjectData,
    actual_text: str,
    analysis_report: dict[str, Any] | None,
) -> list[QCIssue]:
    """Compare expected kept source transcript against actual export transcript."""
    issues: list[QCIssue] = []
    normalized_actual = normalize_text(actual_text)
    keep_ranges = get_final_keep_ranges(project, start_buffer=0.1, end_buffer=0.15)

    def near_kept_range_start(segment: Segment) -> bool:
        return any(0 <= segment.start - range_.start <= 0.75 for range_ in keep_ranges)

    def near_kept_range_end(segment: Segment) -> bool:
        return any(0 <= range_.end - segment.end <= 0.75 for range_ in keep_ranges)

    for index, segment, text in expected_kept_segments(project):
        normalized_text = normalize_text(text)
        if not normalized_text or len(normalized_text) < 8:
            continue

        score = fuzz.partial_ratio(normalized_text, normalized_actual)
        if score < SEGMENT_MATCH_THRESHOLD:
            issues.append(QCIssue(
                severity="high",
                type="kept_segment_missing_or_changed",
                source_start=segment.start,
                source_end=segment.end,
                segment_indices=[index],
                reason="A kept segment does not substantially appear in the independently transcribed export.",
                recommendation="Review the source/export around this segment; restore or widen the range if the take was cut.",
                evidence={"match_score": score, "text": text},
            ))
            continue

        words = content_words(text)
        if len(words) < 2:
            continue

        first_phrase = phrase(words, first=True)
        last_phrase = phrase(words, first=False)
        first_score = fuzz.partial_ratio(first_phrase, normalized_actual) if first_phrase else 100
        last_score = fuzz.partial_ratio(last_phrase, normalized_actual) if last_phrase else 100

        if near_kept_range_start(segment) and first_score < EDGE_PHRASE_THRESHOLD:
            issues.append(QCIssue(
                severity="high",
                type="possible_first_words_cut",
                source_start=segment.start,
                source_end=segment.end,
                segment_indices=[index],
                reason="The beginning words of a kept segment are weakly represented in the export transcript.",
                recommendation="Review and widen the start boundary for this segment.",
                evidence={"first_phrase": first_phrase, "match_score": first_score},
            ))

        if near_kept_range_end(segment) and last_score < EDGE_PHRASE_THRESHOLD:
            issues.append(QCIssue(
                severity="high",
                type="possible_last_words_cut",
                source_start=segment.start,
                source_end=segment.end,
                segment_indices=[index],
                reason="The ending words of a kept segment are weakly represented in the export transcript.",
                recommendation="Review and widen the end boundary for this segment.",
                evidence={"last_phrase": last_phrase, "match_score": last_score},
            ))

    for index, segment, text in expected_removed_segments(project):
        normalized_text = normalize_text(text)
        if len(normalized_text) < 18:
            continue

        score = fuzz.partial_ratio(normalized_text, normalized_actual)
        if score >= REMOVED_TEXT_THRESHOLD:
            severity = "medium" if _retake_similarity_exemption(analysis_report, index) else "high"
            issues.append(QCIssue(
                severity=severity,
                type="removed_text_appears_in_export",
                source_start=segment.start,
                source_end=segment.end,
                segment_indices=[index],
                reason="Text from a removed source segment appears in the independently transcribed export.",
                recommendation=(
                    "If this was a retake with nearly identical wording, compare by playback; "
                    "otherwise the bad take may still be present."
                ),
                evidence={"match_score": score, "text": text},
            ))

    return issues


def _check_boundaries(project: ProjectData, keep_ranges: list[TimeRange]) -> list[QCIssue]:
    """Check source token boundaries for risky cuts."""
    issues: list[QCIssue] = []
    tokens = sorted(project.tokens, key=lambda token: token.start)
    if not tokens:
        return issues

    for range_index, range_ in enumerate(keep_ranges):
        range_tokens = [
            token for token in tokens
            if _token_is_kept(project, token)
            and range_.start <= token.start
            and token.end <= range_.end
        ]
        if not range_tokens:
            continue

        first_token = range_tokens[0]
        last_token = range_tokens[-1]
        start_margin = first_token.start - range_.start
        end_margin = range_.end - last_token.end
        previous_token = next(
            (
                token for token in reversed(tokens)
                if token.end <= first_token.start and token is not first_token
            ),
            None,
        )
        next_token = next(
            (
                token for token in tokens
                if token.start >= last_token.end and token is not last_token
            ),
            None,
        )

        if start_margin < BOUNDARY_START_MARGIN:
            blocked_by_removed = (
                previous_token is not None
                and not _token_is_kept(project, previous_token)
                and previous_token.end >= range_.start - 0.05
            )
            issues.append(QCIssue(
                severity="medium" if blocked_by_removed else "high",
                type="tight_start_boundary_after_removed_token" if blocked_by_removed else "unsafe_start_boundary",
                source_start=range_.start,
                source_end=min(range_.end, range_.start + 2.0),
                reason=(
                    "The kept range starts close to the first kept token because a removed token is immediately before it."
                    if blocked_by_removed else
                    "The kept range starts too close to the first source token."
                ),
                recommendation=(
                    "Listen to this edit point; widening may reintroduce removed filler or correction speech."
                    if blocked_by_removed else
                    "Widen the start boundary before exporting again."
                ),
                evidence={
                    "range_index": range_index,
                    "first_token": first_token.text,
                    "start_margin": start_margin,
                    "required_margin": BOUNDARY_START_MARGIN,
                    "blocking_token": previous_token.text if blocked_by_removed and previous_token else None,
                },
            ))

        if end_margin < BOUNDARY_END_MARGIN:
            blocked_by_removed = (
                next_token is not None
                and not _token_is_kept(project, next_token)
                and next_token.start <= range_.end + 0.05
            )
            issues.append(QCIssue(
                severity="medium" if blocked_by_removed else "high",
                type="tight_end_boundary_before_removed_token" if blocked_by_removed else "unsafe_end_boundary",
                source_start=max(range_.start, range_.end - 2.0),
                source_end=range_.end,
                reason=(
                    "The kept range ends close to the last kept token because a removed token starts immediately after it."
                    if blocked_by_removed else
                    "The kept range ends too close to the last source token."
                ),
                recommendation=(
                    "Listen to this edit point; widening may reintroduce removed filler or correction speech."
                    if blocked_by_removed else
                    "Widen the end boundary before exporting again."
                ),
                evidence={
                    "range_index": range_index,
                    "last_token": last_token.text,
                    "end_margin": end_margin,
                    "required_margin": BOUNDARY_END_MARGIN,
                    "blocking_token": next_token.text if blocked_by_removed and next_token else None,
                },
            ))

        crossing = [
            token for token in tokens
            if token.start < range_.start < token.end or token.start < range_.end < token.end
        ]
        for token in crossing:
            issues.append(QCIssue(
                severity="high",
                type="cut_inside_token",
                source_start=token.start,
                source_end=token.end,
                reason="A proposed cut boundary crosses a word token.",
                recommendation="Move the cut boundary outside this token.",
                evidence={"range_index": range_index, "token": token.text},
            ))

        non_kept_inside = [
            token for token in tokens
            if not _token_is_kept(project, token)
            and range_.start <= token.start
            and token.end <= range_.end
        ]
        for token in non_kept_inside:
            issues.append(QCIssue(
                severity="medium",
                type="non_kept_token_inside_keep_range",
                source_start=token.start,
                source_end=token.end,
                reason="A token that is not attached to a kept transcript segment is fully inside a kept range.",
                recommendation="Trim the range with repair-boundaries or listen to confirm this word should remain.",
                evidence={"range_index": range_index, "token": token.text},
            ))

    return issues


def _token_is_kept(project: ProjectData, token: Token) -> bool:
    """Return whether a source token belongs to an effectively kept segment."""
    midpoint = (token.start + token.end) / 2
    for index, segment in enumerate(project.segments):
        if segment.start - 0.05 <= midpoint <= segment.end + 0.05:
            return is_segment_kept(project, index)
    return False


def repair_project_boundaries(
    project_path: Path,
    *,
    output_path: Path | None = None,
    start_margin: float = BOUNDARY_START_MARGIN,
    end_margin: float = BOUNDARY_END_MARGIN,
) -> tuple[Path, int]:
    """
    Rewrite project keep ranges so cuts do not cross source tokens.

    Kept tokens are protected by widening ranges. Removed tokens are protected
    by trimming ranges so the export does not leave partial words from removed
    takes.
    """
    project = load_project(project_path)
    ranges = list(project.original_keep_ranges)
    if not ranges:
        ranges = get_final_keep_ranges(
            project,
            start_buffer=0.1,
            end_buffer=0.15,
        )

    adjusted: list[TimeRange] = []
    changes = 0

    for range_ in ranges:
        start = range_.start
        end = range_.end

        for _ in range(3):
            previous = (start, end)

            for token in project.tokens:
                if token.start < start < token.end:
                    if _token_is_kept(project, token):
                        start = max(0.0, token.start - start_margin)
                    else:
                        start = min(end, token.end + 0.02)

                if token.start < end < token.end:
                    if _token_is_kept(project, token):
                        end = min(project.video_duration, token.end + end_margin)
                    else:
                        end = max(start, token.start - 0.02)

            kept_tokens = [
                token for token in project.tokens
                if _token_is_kept(project, token) and start <= token.start and token.end <= end
            ]
            if kept_tokens:
                first = kept_tokens[0]
                last = kept_tokens[-1]
                leading_nonkept = [
                    token for token in project.tokens
                    if not _token_is_kept(project, token)
                    and start <= token.start
                    and token.end <= first.start
                ]
                if leading_nonkept:
                    start = max(start, leading_nonkept[-1].end + 0.02)

                trailing_nonkept = [
                    token for token in project.tokens
                    if not _token_is_kept(project, token)
                    and last.end <= token.start
                    and token.end <= end
                ]
                if trailing_nonkept:
                    end = min(end, trailing_nonkept[0].start - 0.02)

                if first.start - start < start_margin:
                    desired_start = max(0.0, first.start - start_margin)
                    removed_before = [
                        token for token in project.tokens
                        if not _token_is_kept(project, token)
                        and desired_start <= token.end <= first.start
                    ]
                    if removed_before:
                        desired_start = max(desired_start, removed_before[-1].end + 0.02)
                    start = min(start, desired_start)
                if end - last.end < end_margin:
                    desired_end = min(project.video_duration, last.end + end_margin)
                    removed_after = [
                        token for token in project.tokens
                        if not _token_is_kept(project, token)
                        and last.end <= token.start <= desired_end
                    ]
                    if removed_after:
                        desired_end = min(desired_end, removed_after[0].start - 0.02)
                    end = max(end, desired_end)

            if previous == (start, end):
                break

        if abs(start - range_.start) > 0.001 or abs(end - range_.end) > 0.001:
            changes += 1

        if end > start + 0.05:
            pieces = [TimeRange(start, end)]
            for token in project.tokens:
                if _token_is_kept(project, token):
                    continue

                next_pieces: list[TimeRange] = []
                for piece in pieces:
                    if piece.start < token.start and token.end < piece.end:
                        before = TimeRange(piece.start, token.start - 0.02)
                        after = TimeRange(token.end + 0.02, piece.end)
                        if before.end > before.start + 0.05:
                            next_pieces.append(before)
                        if after.end > after.start + 0.05:
                            next_pieces.append(after)
                        changes += 1
                    else:
                        next_pieces.append(piece)
                pieces = next_pieces

            adjusted.extend(pieces)

    adjusted = merge_ranges(adjusted)
    output = Path(output_path).expanduser().resolve() if output_path else project.path
    project.raw["original_keep_ranges"] = [
        {"start": range_.start, "end": range_.end}
        for range_ in adjusted
    ]
    project.raw.setdefault("codex_analysis", {})["boundary_repair"] = {
        "source_project": str(project.path),
        "adjusted_ranges": changes,
        "start_margin": start_margin,
        "end_margin": end_margin,
    }
    write_project(output, project.raw)
    return output, changes


def _check_bad_take_markers(project: ProjectData) -> list[QCIssue]:
    """Flag kept segments that still look like bad takes from transcript markers."""
    issues: list[QCIssue] = []

    correction_markers = {
        marker for marker, marker_type in HUNGARIAN_HESITATION_MARKERS.items()
        if marker_type == "correction_signal"
    }
    hesitation_markers = set(HUNGARIAN_HESITATION_MARKERS) - correction_markers

    for index, segment, text in expected_kept_segments(project):
        normalized = normalize_text(text)
        words = normalized.split()
        if not words:
            continue

        corrections = [marker for marker in correction_markers if re.search(rf"\b{re.escape(marker)}\b", normalized)]
        hesitations = [marker for marker in hesitation_markers if re.search(rf"\b{re.escape(marker)}\b", normalized)]

        if corrections:
            issues.append(QCIssue(
                severity="medium",
                type="kept_self_correction_marker",
                source_start=segment.start,
                source_end=segment.end,
                segment_indices=[index],
                reason="A kept segment contains self-correction markers.",
                recommendation="Listen to this segment and cut it if it is an abandoned take.",
                evidence={"markers": sorted(corrections), "text": text},
            ))

        if len(hesitations) >= 2:
            issues.append(QCIssue(
                severity="medium",
                type="kept_hesitation_markers",
                source_start=segment.start,
                source_end=segment.end,
                segment_indices=[index],
                reason="A kept segment contains multiple hesitation markers.",
                recommendation="Listen to this segment and compare against nearby retakes.",
                evidence={"markers": sorted(hesitations), "text": text},
            ))

    return issues


def _check_retakes(analysis_report: dict[str, Any] | None) -> list[QCIssue]:
    """Review retake group decisions from the original analysis report."""
    if not analysis_report:
        return []

    issues: list[QCIssue] = []
    for group in analysis_report.get("retake_groups", []):
        selected_number = group.get("selected_take")
        selected = None
        for take in group.get("takes", []):
            if take.get("take_number") == selected_number:
                selected = take
                break
        if not selected:
            issues.append(QCIssue(
                severity="high",
                type="retake_group_has_no_selected_take",
                reason=f"Retake group {group.get('id')} has no selected take in the report.",
                recommendation="Review this retake group manually.",
                evidence={"group": group},
            ))
            continue

        selected_text = normalize_text(selected.get("text", ""))
        for take in group.get("takes", []):
            if take.get("take_number") == selected_number:
                continue

            removed_text = normalize_text(take.get("text", ""))
            if not removed_text:
                continue

            similarity = fuzz.token_set_ratio(removed_text, selected_text)
            removed_words = set(removed_text.split())
            selected_words = set(selected_text.split())
            unique_words = sorted(word for word in removed_words - selected_words if len(word) > 3)

            if similarity < 55 and len(unique_words) >= 3:
                issues.append(QCIssue(
                    severity="medium",
                    type="removed_take_has_unique_content",
                    source_start=take.get("start"),
                    source_end=take.get("end"),
                    segment_indices=take.get("segment_indices", []),
                    reason="A removed retake appears to contain content not covered by the selected take.",
                    recommendation="Compare the removed and selected takes before finalizing.",
                    evidence={
                        "group_id": group.get("id"),
                        "take_number": take.get("take_number"),
                        "selected_take": selected_number,
                        "similarity": similarity,
                        "unique_words": unique_words[:12],
                    },
                ))

    return issues


def _check_silent_visuals(
    *,
    project: ProjectData,
    min_gap: float,
    motion_threshold: float,
) -> list[QCIssue]:
    """Flag silent source ranges that still show visual movement."""
    ranges = detect_silent_visual_ranges(
        video_path=project.video_path,
        segments=project.segments,
        video_duration=project.video_duration,
        min_gap=min_gap,
        motion_threshold=motion_threshold,
    )
    return [
        QCIssue(
            severity="medium",
            type="silent_visual_context",
            source_start=item.start,
            source_end=item.end,
            reason=f"No speech was detected, but screen frames changed. Motion score: {item.motion_score:.2f}.",
            recommendation="Review this source range and add a highlight if the screen action matters.",
            evidence={"motion_score": item.motion_score, "sampled_frames": item.sampled_frames},
        )
        for item in ranges
    ]


def _range_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _issue_overlaps_highlight(issue: QCIssue, project: ProjectData) -> bool:
    if issue.source_start is None or issue.source_end is None:
        return False
    issue_duration = max(0.001, issue.source_end - issue.source_start)
    for highlight in project.highlight_regions:
        try:
            start = float(highlight["start"])
            end = float(highlight["end"])
        except (KeyError, TypeError, ValueError):
            continue
        overlap = _range_overlap(issue.source_start, issue.source_end, start, end)
        if overlap >= min(0.5, issue_duration * 0.5):
            return True
    return False


def _classify_issues(project: ProjectData, issues: list[QCIssue]) -> list[QCIssue]:
    """Attach review-status categories so reports separate risk from accepted tradeoffs."""
    accepted_boundary_types = {
        "tight_start_boundary_after_removed_token",
        "tight_end_boundary_before_removed_token",
    }

    for issue in issues:
        issue.review_status = "unresolved_review"

        if issue.severity == "high":
            continue

        if issue.type in accepted_boundary_types:
            issue.review_status = "accepted_style"
            continue

        if issue.type == "silent_visual_context" and _issue_overlaps_highlight(issue, project):
            issue.review_status = "accepted_style"
            continue

        if issue.type == "non_kept_token_inside_keep_range" and _issue_overlaps_highlight(issue, project):
            issue.review_status = "accepted_style"
            continue

        if issue.type == "kept_self_correction_marker":
            markers = set((issue.evidence or {}).get("markers", []))
            if markers == {"nem"}:
                issue.review_status = "likely_false_positive"

    return issues


def _segment_context(project: ProjectData, start: float, end: float) -> dict[str, Any]:
    previous = next(
        (
            segment for segment in reversed(project.segments)
            if segment.end <= start
        ),
        None,
    )
    following = next(
        (
            segment for segment in project.segments
            if segment.start >= end
        ),
        None,
    )
    return {
        "previous": {
            "start": previous.start,
            "end": previous.end,
            "text": previous.text,
        } if previous else None,
        "next": {
            "start": following.start,
            "end": following.end,
            "text": following.text,
        } if following else None,
    }


def write_silent_visual_contact_sheets(
    *,
    project_path: Path,
    output_dir: Path,
    min_gap: float = 2.0,
    motion_threshold: float = 0.2,
    config: Config | None = None,
) -> list[dict[str, Any]]:
    """Write cropped contact sheets for silent visual ranges and return a review manifest."""
    project = load_project(project_path)
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    config = config or Config(temp_dir=Path(tempfile.gettempdir()) / "video_editor")

    ranges = detect_silent_visual_ranges(
        video_path=project.video_path,
        segments=project.segments,
        video_duration=project.video_duration,
        min_gap=min_gap,
        motion_threshold=motion_threshold,
    )
    crop_filter = crop_filter_from_project(project, Cutter(config))
    manifest: list[dict[str, Any]] = []

    for index, item in enumerate(ranges, 1):
        duration = max(0.1, item.end - item.start)
        frame_count = min(6, max(2, int(round(duration))))
        fps = max(0.2, min(1.0, frame_count / duration))
        filters = []
        if crop_filter:
            filters.append(crop_filter)
        filters.extend([
            f"fps={fps:.4f}",
            "scale=480:-1",
            f"tile={frame_count}x1",
        ])
        output_path = output_dir / f"{index:03d}_{format_time(item.start).replace(':', '-')}.png"
        cmd = [
            FFMPEG,
            "-y",
            "-hide_banner", "-loglevel", "error", "-nostats",
            "-ss", f"{item.start:.3f}",
            "-t", f"{duration:.3f}",
            "-i", str(project.video_path),
            "-an",
            "-vf", ",".join(filters),
            "-frames:v", "1",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        sheet_path = output_path if result.returncode == 0 and output_path.exists() else None
        manifest.append({
            "index": index,
            "start": item.start,
            "end": item.end,
            "duration": duration,
            "motion_score": item.motion_score,
            "sampled_frames": item.sampled_frames,
            "contact_sheet": str(sheet_path) if sheet_path else None,
            "crop_filter": crop_filter,
            "suggested_action": "review",
            "context": _segment_context(project, item.start, item.end),
        })

    return manifest


def write_silent_visual_review_files(
    manifest: list[dict[str, Any]],
    *,
    json_path: Path,
    markdown_path: Path,
) -> tuple[Path, Path]:
    """Write review-silence JSON and Markdown artifacts."""
    json_path = Path(json_path).expanduser().resolve()
    markdown_path = Path(markdown_path).expanduser().resolve()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)

    json_path.write_text(json.dumps({"ranges": manifest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Silent Visual Review",
        "",
        f"- Ranges: {len(manifest)}",
        "",
    ]
    for item in manifest:
        lines.append(
            f"{item['index']}. {format_time(item['start'])}-{format_time(item['end'])} "
            f"(motion {item['motion_score']:.2f})"
        )
        if item.get("contact_sheet"):
            lines.append(f"   - Contact sheet: `{item['contact_sheet']}`")
        context = item.get("context", {})
        if context.get("previous"):
            lines.append(f"   - Before: {context['previous']['text']}")
        if context.get("next"):
            lines.append(f"   - After: {context['next']['text']}")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def _create_review_clip(source_video: Path, output_path: Path, start: float, end: float) -> None:
    """Create a small review clip around a source issue."""
    clip_start = max(0.0, start - 1.0)
    clip_duration = max(0.5, end - start + 2.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner", "-loglevel", "error", "-nostats",
        "-ss", f"{clip_start:.3f}",
        "-t", f"{clip_duration:.3f}",
        "-i", str(source_video),
        "-c", "copy",
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=False)


def _write_review_clips(result: QCResult, review_dir: Path) -> list[Path]:
    """Write source review clips for timestamped medium/high issues."""
    review_dir = Path(review_dir).expanduser().resolve()
    review_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []

    for index, issue in enumerate(result.issues, 1):
        if issue.severity not in {"high", "medium"}:
            continue
        if issue.source_start is None or issue.source_end is None:
            continue
        output_path = review_dir / f"{index:03d}_{issue.type}_{format_time(issue.source_start).replace(':', '-')}.mp4"
        _create_review_clip(result.source_video, output_path, issue.source_start, issue.source_end)
        if output_path.exists():
            clips.append(output_path)
    return clips


def _status_from_issues(issues: list[QCIssue]) -> str:
    if any(issue.severity == "high" for issue in issues):
        return "needs_fix"
    if any(
        issue.severity == "medium" and issue.review_status == "unresolved_review"
        for issue in issues
    ):
        return "needs_review"
    return "pass"


def run_quality_control(
    *,
    project_path: Path,
    export_video: Path,
    analysis_path: Path | None = None,
    config: Config | None = None,
    skip_export_transcription: bool = False,
    silent_visual_min_gap: float = 2.0,
    visual_motion_threshold: float = 0.2,
) -> QCResult:
    """Run second-pass QC against a project and exported video."""
    project = load_project(project_path)
    export_video = Path(export_video).expanduser().resolve()
    if not export_video.exists():
        raise FileNotFoundError(f"Exported video not found: {export_video}")

    config = config or Config(temp_dir=Path(tempfile.gettempdir()) / "video_editor")
    if config.temp_dir:
        config.temp_dir.mkdir(parents=True, exist_ok=True)

    analysis_report = _load_analysis_report(project, analysis_path)
    keep_ranges = get_final_keep_ranges(
        project,
        start_buffer=config.segment_start_buffer,
        end_buffer=config.segment_end_buffer,
    )

    expected_text = transcript_text([
        Segment(start=segment.start, end=segment.end, text=text, confidence=segment.confidence)
        for _, segment, text in expected_kept_segments(project)
    ])

    actual_segments: list[Segment] = []
    actual_text = ""
    if not skip_export_transcription:
        actual_segments, _ = _transcribe_export(export_video, config)
        actual_text = transcript_text(actual_segments)

    issues: list[QCIssue] = []

    if actual_text:
        issues.extend(_check_export_transcript(
            project=project,
            actual_text=actual_text,
            analysis_report=analysis_report,
        ))
    else:
        issues.append(QCIssue(
            severity="medium",
            type="export_transcript_not_checked",
            reason="The exported video was not independently transcribed.",
            recommendation="Run QC without --skip-export-transcription before calling the edit final.",
        ))

    issues.extend(_check_boundaries(project, keep_ranges))
    issues.extend(_check_bad_take_markers(project))
    issues.extend(_check_retakes(analysis_report))
    issues.extend(_check_silent_visuals(
        project=project,
        min_gap=silent_visual_min_gap,
        motion_threshold=visual_motion_threshold,
    ))

    expected_normalized = normalize_text(expected_text)
    actual_normalized = normalize_text(actual_text)
    similarity = (
        fuzz.token_set_ratio(expected_normalized, actual_normalized)
        if expected_normalized and actual_normalized
        else 0.0
    )

    issues = _classify_issues(project, issues)

    result = QCResult(
        status=_status_from_issues(issues),
        project_path=Path(project_path).expanduser().resolve(),
        source_video=project.video_path.expanduser().resolve(),
        export_video=export_video,
        export_duration=_export_duration(export_video),
        expected_transcript=expected_text,
        actual_transcript=actual_text,
        expected_actual_similarity=similarity,
        issues=sorted(
            issues,
            key=lambda issue: (
                {"high": 0, "medium": 1, "low": 2}.get(issue.severity, 3),
                {"unresolved_review": 0, "accepted_style": 1, "likely_false_positive": 2}.get(issue.review_status, 3),
                issue.source_start if issue.source_start is not None else float("inf"),
            ),
        ),
    )
    return result


def write_qc_json(result: QCResult, path: Path) -> Path:
    """Write the machine-readable QC report."""
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(result.to_dict(), file, ensure_ascii=False, indent=2)
        file.write("\n")
    return path


def write_qc_markdown(result: QCResult, path: Path) -> Path:
    """Write the human-readable QC report."""
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    summary = issue_summary(result.issues)
    lines = [
        "# Video QC Report",
        "",
        f"- Status: `{result.status}`",
        f"- Project: `{result.project_path}`",
        f"- Source: `{result.source_video}`",
        f"- Export: `{result.export_video}`",
        f"- Export duration: {format_time(result.export_duration)}",
        f"- Expected/export transcript similarity: {result.expected_actual_similarity:.1f}",
        f"- Issues: {len(result.issues)}",
        f"- High: {summary['high']}",
        f"- Unresolved review: {summary['unresolved_review']}",
        f"- Accepted style: {summary['accepted_style']}",
        f"- Likely false positive: {summary['likely_false_positive']}",
        "",
    ]

    if result.issues:
        lines.extend(["## Issues", ""])
        for index, issue in enumerate(result.issues, 1):
            timestamp = ""
            if issue.source_start is not None and issue.source_end is not None:
                timestamp = f" {format_time(issue.source_start)}-{format_time(issue.source_end)}"
            lines.append(
                f"{index}. [{issue.severity}/{issue.review_status}] `{issue.type}`{timestamp}: {issue.reason} "
                f"Recommendation: {issue.recommendation}"
            )
        lines.append("")
    else:
        lines.extend(["No QC issues detected.", ""])

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_qc_review_clips(result: QCResult, review_dir: Path | None) -> list[Path]:
    """Optionally write review clips for timestamped issues."""
    if review_dir is None:
        return []
    return _write_review_clips(result, review_dir)
