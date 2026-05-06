"""Reusable analysis pipeline for headless/Codex workflows."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from .analyzer import Analyzer, SegmentAction, TimeRange
from .config import Config
from .cutter import Cutter
from .project_io import (
    build_project_payload,
    format_time,
    merge_ranges,
    write_project,
)
from .qc import QualityController
from .transcriber import Segment, Token, Transcriber
from .visual_analysis import SilentVisualRange, detect_silent_visual_ranges, iter_silent_gaps

console = Console()


@dataclass
class SegmentDecision:
    """Decision for a single transcript segment."""

    index: int
    start: float
    end: float
    text: str
    action: str
    reason: str
    confidence: float
    retake_group_id: int | None = None


@dataclass
class CutRange:
    """A range removed from the AI-generated output."""

    start: float
    end: float
    reason: str
    confidence: float
    text: str = ""
    segment_indices: list[int] | None = None


@dataclass
class QuestionableRange:
    """A range that should be reviewed before trusting the first pass."""

    start: float
    end: float
    type: str
    severity: str
    reason: str
    recommended_action: str
    segment_indices: list[int] | None = None
    motion_score: float | None = None


@dataclass
class RetakeTakeReport:
    """One take inside a retake group."""

    take_number: int
    start: float
    end: float
    text: str
    selected: bool
    segment_indices: list[int]


@dataclass
class RetakeGroupReport:
    """Structured retake group details."""

    id: int
    selected_take: int
    reason: str
    takes: list[RetakeTakeReport]


@dataclass
class AnalysisResult:
    """Complete result of analyzing a recording."""

    video_path: Path
    duration: float
    segments: list[Segment]
    tokens: list[Token]
    decisions: list[SegmentDecision]
    keep_ranges: list[TimeRange]
    cut_ranges: list[CutRange]
    retake_groups: list[RetakeGroupReport]
    questionable_ranges: list[QuestionableRange]

    @property
    def kept_duration(self) -> float:
        return sum(range_.duration for range_ in self.keep_ranges)

    @property
    def removed_duration(self) -> float:
        return max(0.0, self.duration - self.kept_duration)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-safe dictionary."""
        return {
            "video_path": str(self.video_path),
            "duration": self.duration,
            "summary": {
                "segments_total": len(self.segments),
                "segments_kept": sum(1 for decision in self.decisions if decision.action == SegmentAction.KEEP.value),
                "segments_removed": sum(1 for decision in self.decisions if decision.action == SegmentAction.REMOVE.value),
                "kept_duration": self.kept_duration,
                "removed_duration": self.removed_duration,
                "questionable_count": len(self.questionable_ranges),
                "retake_group_count": len(self.retake_groups),
            },
            "segments": [asdict(decision) for decision in self.decisions],
            "keep_ranges": [asdict(range_) for range_ in self.keep_ranges],
            "cut_ranges": [asdict(range_) for range_ in self.cut_ranges],
            "retake_groups": [
                {
                    "id": group.id,
                    "selected_take": group.selected_take,
                    "reason": group.reason,
                    "takes": [asdict(take) for take in group.takes],
                }
                for group in self.retake_groups
            ],
            "questionable_ranges": [asdict(range_) for range_ in self.questionable_ranges],
        }


def _segment_indices_for_range(segments: list[Segment], start: float, end: float) -> list[int]:
    """Return original transcript segment indices contained in a range."""
    return [
        index for index, segment in enumerate(segments)
        if segment.start >= start - 0.05 and segment.end <= end + 0.05
    ]


def _tokens_for_segment(tokens: list[Token], segment: Segment) -> list[Token]:
    """Return word tokens that belong to a transcript segment."""
    return [
        token for token in tokens
        if segment.start - 0.05 <= token.start and token.end <= segment.end + 0.05
    ]


def _optimized_speech_ranges(
    *,
    segments: list[Segment],
    tokens: list[Token],
    decisions: list[SegmentDecision],
    duration: float,
    config: Config,
    strict: bool,
) -> list[TimeRange]:
    """
    Build speech keep ranges using token-aware conservative boundaries.

    This deliberately widens around the first and last word. Leaving a small
    pause is preferable to cutting a phoneme.
    """
    start_padding = max(config.segment_start_buffer, 0.22 if strict else 0.16)
    end_padding = max(config.segment_end_buffer, 0.34 if strict else 0.24)

    ranges: list[TimeRange] = []
    for decision in decisions:
        if decision.action != SegmentAction.KEEP.value:
            continue

        segment = segments[decision.index]
        segment_tokens = _tokens_for_segment(tokens, segment)
        if segment_tokens:
            start = min(token.start for token in segment_tokens)
            end = max(token.end for token in segment_tokens)
        else:
            start = segment.start
            end = segment.end

        ranges.append(TimeRange(
            start=max(0.0, start - start_padding),
            end=min(duration, end + end_padding),
        ))

    return merge_ranges(ranges)


def _silence_cut_ranges(
    *,
    segments: list[Segment],
    duration: float,
    min_gap: float,
) -> list[CutRange]:
    """Represent meaningful no-speech gaps as reportable cuts."""
    ranges: list[CutRange] = []
    for start, end in iter_silent_gaps(segments, duration, min_gap):
        ranges.append(CutRange(
            start=start,
            end=end,
            reason="silent_gap",
            confidence=0.8,
            text="",
            segment_indices=[],
        ))
    return ranges


def _quality_questionables(
    analyzer: Analyzer,
    decisions: list[SegmentDecision],
    segments: list[Segment],
) -> list[QuestionableRange]:
    """Flag kept speech that has objective quality markers."""
    ranges: list[QuestionableRange] = []

    for decision in decisions:
        if decision.action != SegmentAction.KEEP.value:
            continue

        segment = segments[decision.index]
        metrics = analyzer._compute_take_metrics(segment, decision.index)
        reasons: list[str] = []
        severity = "low"

        if metrics.incomplete_sentence:
            reasons.append("kept segment may be incomplete")
            severity = "high"
        if metrics.correction_signals > 0:
            reasons.append(f"{metrics.correction_signals} self-correction marker(s)")
            severity = "medium" if severity == "low" else severity
        if metrics.hesitation_count >= 2:
            reasons.append(f"{metrics.hesitation_count} hesitation marker(s)")
            severity = "medium" if severity == "low" else severity

        if reasons:
            ranges.append(QuestionableRange(
                start=segment.start,
                end=segment.end,
                type="speech_quality",
                severity=severity,
                reason="; ".join(reasons),
                recommended_action="review_transcript_and_playback",
                segment_indices=[decision.index],
            ))

    return ranges


def _silent_visual_questionables(ranges: list[SilentVisualRange]) -> list[QuestionableRange]:
    """Convert visual analysis ranges into report ranges."""
    return [
        QuestionableRange(
            start=item.start,
            end=item.end,
            type="silent_visual_context",
            severity="medium",
            reason=f"{item.reason} Motion score: {item.motion_score:.2f}.",
            recommended_action="review_or_include_with_highlight",
            segment_indices=[],
            motion_score=item.motion_score,
        )
        for item in ranges
    ]


def analyze_recording(
    video_path: Path,
    *,
    config: Config | None = None,
    strict: bool = True,
    skip_qc: bool = False,
    qc_report_only: bool = False,
    suggest_silent_visual_ranges: bool = True,
    silent_visual_min_gap: float = 2.0,
    visual_motion_threshold: float = 0.2,
) -> AnalysisResult:
    """Transcribe and analyze a recording without creating a final export."""
    video_path = Path(video_path).expanduser().resolve()
    config = config or Config(temp_dir=Path(tempfile.gettempdir()) / "video_editor")
    if config.temp_dir:
        config.temp_dir.mkdir(parents=True, exist_ok=True)

    cutter = Cutter(config)
    transcriber = Transcriber(config)
    analyzer = Analyzer(config)

    duration = cutter.get_video_duration(video_path)
    segments, tokens = transcriber.transcribe_video(video_path)
    if not segments:
        raise RuntimeError("No speech detected in recording")

    if not skip_qc:
        qc = QualityController(config, auto_correct=not qc_report_only)
        if qc.is_available():
            qc_report = qc.check_segments(segments)
            if not qc_report_only:
                segments = qc.apply_corrections(segments, qc_report)

    retake_groups = analyzer.detect_retakes(segments)
    retake_groups = analyzer.select_best_takes(retake_groups)

    decisions = [
        SegmentDecision(
            index=index,
            start=segment.start,
            end=segment.end,
            text=segment.text,
            action=SegmentAction.KEEP.value,
            reason="kept",
            confidence=0.75,
        )
        for index, segment in enumerate(segments)
    ]

    cut_ranges: list[CutRange] = []
    retake_reports: list[RetakeGroupReport] = []

    for group in retake_groups:
        if group.best_index is None:
            continue

        takes: list[RetakeTakeReport] = []
        for take_index, analyzed in enumerate(group.segments):
            take_segment = analyzed.segment
            segment_indices = _segment_indices_for_range(
                segments,
                take_segment.start,
                take_segment.end,
            )
            selected = take_index == group.best_index
            takes.append(RetakeTakeReport(
                take_number=take_index + 1,
                start=take_segment.start,
                end=take_segment.end,
                text=take_segment.text,
                selected=selected,
                segment_indices=segment_indices,
            ))

            for segment_index in segment_indices:
                decisions[segment_index].retake_group_id = group.id

            if selected:
                for segment_index in segment_indices:
                    decisions[segment_index].reason = "selected_best_retake"
                    decisions[segment_index].confidence = 0.9
                continue

            for segment_index in segment_indices:
                decisions[segment_index].action = SegmentAction.REMOVE.value
                decisions[segment_index].reason = "non_selected_retake"
                decisions[segment_index].confidence = 0.92

            cut_ranges.append(CutRange(
                start=take_segment.start,
                end=take_segment.end,
                reason="non_selected_retake",
                confidence=0.92,
                text=take_segment.text,
                segment_indices=segment_indices,
            ))

        retake_reports.append(RetakeGroupReport(
            id=group.id,
            selected_take=group.best_index + 1,
            reason=group.selection_reason or "selected by analyzer",
            takes=takes,
        ))

    keep_ranges = _optimized_speech_ranges(
        segments=segments,
        tokens=tokens,
        decisions=decisions,
        duration=duration,
        config=config,
        strict=strict,
    )

    cut_ranges.extend(_silence_cut_ranges(
        segments=segments,
        duration=duration,
        min_gap=max(config.silence_threshold, silent_visual_min_gap),
    ))

    questionable_ranges = _quality_questionables(analyzer, decisions, segments)

    if suggest_silent_visual_ranges:
        console.print("[blue]Checking silent gaps for screen activity...[/blue]")
        visual_ranges = detect_silent_visual_ranges(
            video_path=video_path,
            segments=segments,
            video_duration=duration,
            min_gap=silent_visual_min_gap,
            motion_threshold=visual_motion_threshold,
        )
        questionable_ranges.extend(_silent_visual_questionables(visual_ranges))

    return AnalysisResult(
        video_path=video_path,
        duration=duration,
        segments=segments,
        tokens=tokens,
        decisions=decisions,
        keep_ranges=keep_ranges,
        cut_ranges=sorted(cut_ranges, key=lambda item: item.start),
        retake_groups=retake_reports,
        questionable_ranges=sorted(questionable_ranges, key=lambda item: item.start),
    )


def write_analysis_json(result: AnalysisResult, path: Path) -> Path:
    """Write the machine-readable analysis report."""
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(result.to_dict(), file, ensure_ascii=False, indent=2)
        file.write("\n")
    return path


def write_analysis_markdown(result: AnalysisResult, path: Path) -> Path:
    """Write a compact human-readable analysis report."""
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Analysis Report",
        "",
        f"- Source: `{result.video_path}`",
        f"- Duration: {format_time(result.duration)}",
        f"- Kept duration estimate: {format_time(result.kept_duration)}",
        f"- Removed duration estimate: {format_time(result.removed_duration)}",
        f"- Segments: {len(result.segments)} total, "
        f"{sum(1 for decision in result.decisions if decision.action == SegmentAction.REMOVE.value)} removed",
        f"- Questionable ranges: {len(result.questionable_ranges)}",
        "",
    ]

    if result.questionable_ranges:
        lines.extend(["## Questionable Ranges", ""])
        for index, item in enumerate(result.questionable_ranges, 1):
            lines.append(
                f"{index}. {format_time(item.start)}-{format_time(item.end)} "
                f"({item.severity}, {item.type}): {item.reason} "
                f"Recommended: {item.recommended_action}."
            )
        lines.append("")

    if result.retake_groups:
        lines.extend(["## Retake Groups", ""])
        for group in result.retake_groups:
            lines.append(
                f"- Group {group.id}: selected take {group.selected_take}. {group.reason}"
            )
            for take in group.takes:
                marker = "selected" if take.selected else "removed"
                preview = take.text.replace("\n", " ")[:120]
                lines.append(
                    f"  - Take {take.take_number} ({marker}) "
                    f"{format_time(take.start)}-{format_time(take.end)}: {preview}"
                )
        lines.append("")

    removed_takes = [
        item for item in result.cut_ranges
        if item.reason == "non_selected_retake"
    ]
    if removed_takes:
        lines.extend(["## Removed Takes", ""])
        for index, item in enumerate(removed_takes, 1):
            preview = item.text.replace("\n", " ")[:160]
            lines.append(
                f"{index}. {format_time(item.start)}-{format_time(item.end)}: {preview}"
            )
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_analysis_project(
    result: AnalysisResult,
    path: Path,
    *,
    metadata: dict[str, Any] | None = None,
    auto_include_visual_ranges: bool = False,
) -> Path:
    """Write an editable project whose source remains the original recording."""
    highlights: list[dict[str, Any]] = []
    if auto_include_visual_ranges:
        for item in result.questionable_ranges:
            if item.type == "silent_visual_context":
                highlights.append({
                    "start": item.start,
                    "end": item.end,
                    "label": "AI suggested screen context",
                })

    analyzed = [
        {
            "action": decision.action,
            "reason": decision.reason,
            "retake_group_id": decision.retake_group_id,
        }
        for decision in result.decisions
    ]

    payload = build_project_payload(
        video_path=result.video_path,
        video_duration=result.duration,
        segments=result.segments,
        tokens=result.tokens,
        analyzed=analyzed,
        keep_ranges=result.keep_ranges,
        highlight_regions=highlights,
        metadata={
            **(metadata or {}),
            "questionable_ranges": [asdict(item) for item in result.questionable_ranges],
            "retake_groups": [
                {
                    "id": group.id,
                    "selected_take": group.selected_take,
                    "reason": group.reason,
                }
                for group in result.retake_groups
            ],
        },
    )
    return write_project(path, payload)

