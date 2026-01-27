"""Data models for GUI state management."""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..transcriber import Segment, Token
from ..analyzer import AnalyzedSegment, TimeRange, SegmentAction


@dataclass
class HighlightRegion:
    """A user-defined region to force-include in export (for non-speech content)."""
    start: float
    end: float
    label: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class EditSession:
    """
    Stores the editable state for a video editing session.

    This class tracks:
    - Original transcription data (segments and tokens)
    - Analysis results (what the AI decided to keep/cut)
    - User modifications (text edits and keep/cut overrides)
    """
    video_path: Path
    video_duration: float
    original_segments: list[Segment] = field(default_factory=list)
    analyzed_segments: list[AnalyzedSegment] = field(default_factory=list)
    tokens: list[Token] = field(default_factory=list)
    original_keep_ranges: list[TimeRange] = field(default_factory=list)

    # User modifications
    text_edits: dict[int, str] = field(default_factory=dict)  # segment_index → edited_text
    keep_overrides: dict[int, bool] = field(default_factory=dict)  # segment_index → keep (True/False)
    highlight_regions: list[HighlightRegion] = field(default_factory=list)  # Force-include regions

    def get_segment_text(self, index: int) -> str:
        """Get the current text for a segment (edited or original)."""
        if index in self.text_edits:
            return self.text_edits[index]
        if 0 <= index < len(self.original_segments):
            return self.original_segments[index].text
        return ""

    def set_segment_text(self, index: int, text: str) -> None:
        """Set edited text for a segment."""
        if 0 <= index < len(self.original_segments):
            original = self.original_segments[index].text
            if text == original:
                # Remove edit if it matches original
                self.text_edits.pop(index, None)
            else:
                self.text_edits[index] = text

    def is_segment_kept(self, index: int) -> bool:
        """Check if a segment should be kept (considering overrides)."""
        if index in self.keep_overrides:
            return self.keep_overrides[index]

        # Check original analysis
        if 0 <= index < len(self.analyzed_segments):
            return self.analyzed_segments[index].action == SegmentAction.KEEP

        # Default to True if no analysis
        return True

    def set_segment_kept(self, index: int, keep: bool) -> None:
        """Override keep/cut decision for a segment."""
        if 0 <= index < len(self.original_segments):
            # Check if this matches the original decision
            original_kept = True
            if index < len(self.analyzed_segments):
                original_kept = self.analyzed_segments[index].action == SegmentAction.KEEP

            if keep == original_kept:
                # Remove override if it matches original
                self.keep_overrides.pop(index, None)
            else:
                self.keep_overrides[index] = keep

    def get_segment_reason(self, index: int) -> str:
        """Get the reason why a segment was marked for removal."""
        if 0 <= index < len(self.analyzed_segments):
            return self.analyzed_segments[index].reason
        return ""

    def add_highlight(self, start: float, end: float, label: str = "") -> int:
        """Add a highlight region. Returns the index of the new highlight."""
        highlight = HighlightRegion(start=start, end=end, label=label)
        self.highlight_regions.append(highlight)
        return len(self.highlight_regions) - 1

    def remove_highlight(self, index: int) -> None:
        """Remove a highlight region by index."""
        if 0 <= index < len(self.highlight_regions):
            self.highlight_regions.pop(index)

    def update_highlight(self, index: int, start: float = None, end: float = None, label: str = None) -> None:
        """Update a highlight region's properties."""
        if 0 <= index < len(self.highlight_regions):
            h = self.highlight_regions[index]
            if start is not None:
                h.start = start
            if end is not None:
                h.end = end
            if label is not None:
                h.label = label

    def get_final_segments(self) -> list[Segment]:
        """Get segments with text edits applied."""
        result = []
        for i, seg in enumerate(self.original_segments):
            if self.is_segment_kept(i):
                text = self.get_segment_text(i)
                result.append(Segment(
                    start=seg.start,
                    end=seg.end,
                    text=text,
                    confidence=seg.confidence,
                    tokens=seg.tokens
                ))
        return result

    def get_final_tokens(self) -> list[Token]:
        """
        Get tokens for kept segments with text edits applied.

        For segments with edited text, we regenerate tokens from the edited text
        while preserving the original timing spread across the new words.
        """
        if not self.tokens:
            return []

        result = []
        for i, seg in enumerate(self.original_segments):
            if not self.is_segment_kept(i):
                continue

            # Find tokens that belong to this segment
            seg_tokens = [t for t in self.tokens if seg.start <= t.start < seg.end]

            if i in self.text_edits and seg_tokens:
                # Text was edited - create new tokens from edited text
                edited_text = self.text_edits[i]
                words = edited_text.split()

                if words and seg_tokens:
                    # Distribute timing across the new words
                    start_time = seg_tokens[0].start
                    end_time = seg_tokens[-1].end
                    total_duration = end_time - start_time

                    for j, word in enumerate(words):
                        word_start = start_time + (j / len(words)) * total_duration
                        word_end = start_time + ((j + 1) / len(words)) * total_duration
                        # Add space before word (except first)
                        text = f" {word}" if j > 0 else word
                        result.append(Token(text=text, start=word_start, end=word_end))
            else:
                # No edit - use original tokens
                result.extend(seg_tokens)

        return result

    def get_final_keep_ranges(self, start_buffer: float = 0.1, end_buffer: float = 0.15) -> list[TimeRange]:
        """
        Get the final keep ranges with user overrides and highlights applied.

        Args:
            start_buffer: Buffer before segment start (prevents word cutoff)
            end_buffer: Buffer after segment end (prevents word cutoff)
        """
        ranges = []

        # Add kept speech segments
        for i, seg in enumerate(self.original_segments):
            if self.is_segment_kept(i):
                buffered_start = max(0.0, seg.start - start_buffer)
                buffered_end = min(self.video_duration, seg.end + end_buffer)
                ranges.append(TimeRange(buffered_start, buffered_end))

        # Add highlight regions (force-include, no buffer needed)
        for highlight in self.highlight_regions:
            ranges.append(TimeRange(highlight.start, highlight.end))

        # Merge overlapping ranges
        return self._merge_ranges(ranges)

    def _merge_ranges(self, ranges: list[TimeRange]) -> list[TimeRange]:
        """Merge overlapping or adjacent time ranges."""
        if not ranges:
            return []

        sorted_ranges = sorted(ranges, key=lambda r: r.start)
        merged = [sorted_ranges[0]]

        for current in sorted_ranges[1:]:
            last = merged[-1]
            if current.start <= last.end + 0.05:  # Allow 50ms gap
                merged[-1] = TimeRange(last.start, max(last.end, current.end))
            else:
                merged.append(current)

        return merged

    def get_all_ranges_for_timeline(self) -> list[tuple[TimeRange, bool, int]]:
        """
        Get all time ranges for timeline display.

        Returns:
            List of (TimeRange, is_kept, segment_index) tuples
        """
        result = []
        for i, seg in enumerate(self.original_segments):
            time_range = TimeRange(seg.start, seg.end)
            is_kept = self.is_segment_kept(i)
            result.append((time_range, is_kept, i))
        return result

    def save(self, path: Path) -> None:
        """Save the editing session to a JSON file."""
        data = {
            "version": "1.0",
            "video_path": str(self.video_path),
            "video_duration": self.video_duration,
            "segments": [
                {
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text,
                    "confidence": seg.confidence
                }
                for seg in self.original_segments
            ],
            "tokens": [
                {"text": tok.text, "start": tok.start, "end": tok.end}
                for tok in self.tokens
            ],
            "analyzed": [
                {
                    "action": aseg.action.value,
                    "reason": aseg.reason,
                    "retake_group_id": aseg.retake_group_id
                }
                for aseg in self.analyzed_segments
            ],
            "text_edits": {str(k): v for k, v in self.text_edits.items()},
            "keep_overrides": {str(k): v for k, v in self.keep_overrides.items()},
            "highlight_regions": [
                {"start": h.start, "end": h.end, "label": h.label}
                for h in self.highlight_regions
            ]
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: Path) -> "EditSession":
        """Load an editing session from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        segments = [
            Segment(
                start=s["start"],
                end=s["end"],
                text=s["text"],
                confidence=s.get("confidence", 1.0)
            )
            for s in data["segments"]
        ]

        tokens = [
            Token(text=t["text"], start=t["start"], end=t["end"])
            for t in data.get("tokens", [])
        ]

        analyzed = []
        for i, a in enumerate(data.get("analyzed", [])):
            if i < len(segments):
                analyzed.append(AnalyzedSegment(
                    segment=segments[i],
                    action=SegmentAction(a["action"]),
                    reason=a.get("reason", ""),
                    retake_group_id=a.get("retake_group_id")
                ))

        # Load highlight regions
        highlights = [
            HighlightRegion(start=h["start"], end=h["end"], label=h.get("label", ""))
            for h in data.get("highlight_regions", [])
        ]

        session = cls(
            video_path=Path(data["video_path"]),
            video_duration=data["video_duration"],
            original_segments=segments,
            analyzed_segments=analyzed,
            tokens=tokens,
            text_edits={int(k): v for k, v in data.get("text_edits", {}).items()},
            keep_overrides={int(k): v for k, v in data.get("keep_overrides", {}).items()},
            highlight_regions=highlights
        )

        return session

    def has_unsaved_changes(self) -> bool:
        """Check if there are unsaved user modifications."""
        return bool(self.text_edits) or bool(self.keep_overrides) or bool(self.highlight_regions)
