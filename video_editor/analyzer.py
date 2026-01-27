"""Analysis module for detecting bad takes, pauses, and retakes."""

import os
from dataclasses import dataclass, field
from enum import Enum

from rapidfuzz import fuzz
from rich.console import Console

from .config import Config
from .transcriber import Segment

console = Console()

# Try importing Gemini (primary) - new package
try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

# Try importing OpenAI (fallback)
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class SegmentAction(Enum):
    """Action to take on a segment."""
    KEEP = "keep"
    REMOVE = "remove"
    RETAKE_CANDIDATE = "retake_candidate"


@dataclass
class AnalyzedSegment:
    """A segment with analysis results."""
    segment: Segment
    action: SegmentAction = SegmentAction.KEEP
    reason: str = ""
    retake_group_id: int | None = None


@dataclass
class TimeRange:
    """A time range for video cutting."""
    start: float
    end: float
    
    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class RetakeGroup:
    """A group of segments that are retakes of each other."""
    id: int
    segments: list[AnalyzedSegment] = field(default_factory=list)
    best_index: int | None = None


class Analyzer:
    """Analyzes transcribed segments to detect bad takes and silences."""
    
    def __init__(self, config: Config):
        self.config = config
        self._gemini_client = None
        self._openai_client = None
        
        # Initialize Gemini if available (primary)
        gemini_key = os.getenv("GEMINI_API_KEY")
        if GEMINI_AVAILABLE and gemini_key:
            self._gemini_client = genai.Client(api_key=gemini_key)
            console.print("[green]✓[/green] Using Gemini 3 Flash for take selection")
        elif OPENAI_AVAILABLE and self.config.openai_api_key:
            self._openai_client = OpenAI(api_key=self.config.openai_api_key)
            console.print("[yellow]Using OpenAI as fallback for take selection[/yellow]")
        else:
            console.print("[yellow]No LLM available - will use duration-based selection[/yellow]")
    
    def detect_silences(self, segments: list[Segment], video_duration: float) -> list[TimeRange]:
        """
        Detect silence gaps between segments.
        
        Args:
            segments: List of transcribed segments
            video_duration: Total duration of the video
            
        Returns:
            List of silence time ranges to remove
        """
        silences = []
        
        # Check silence at the beginning
        if segments and segments[0].start > self.config.silence_threshold:
            silences.append(TimeRange(0, segments[0].start))
        
        # Check silences between segments
        for i in range(len(segments) - 1):
            gap_start = segments[i].end
            gap_end = segments[i + 1].start
            gap_duration = gap_end - gap_start
            
            if gap_duration > self.config.silence_threshold:
                silences.append(TimeRange(gap_start, gap_end))
        
        # Check silence at the end
        if segments and (video_duration - segments[-1].end) > self.config.silence_threshold:
            silences.append(TimeRange(segments[-1].end, video_duration))
        
        console.print(f"[yellow]Found {len(silences)} silence gaps to remove[/yellow]")
        return silences
    
    def _get_segment_prefix(self, text: str, min_words: int = 3) -> str:
        """Extract the beginning of a segment for prefix-based matching."""
        words = text.lower().split()
        return " ".join(words[:min_words]) if len(words) >= min_words else text.lower()

    def _find_recording_blocks(self, segments: list[Segment]) -> list[tuple[int, int]]:
        """
        Find continuous recording blocks separated by significant pauses.

        A recording block is a sequence of segments with small gaps between them.
        These represent continuous speech in a single recording attempt.

        Returns:
            List of (start_idx, end_idx) tuples for each block
        """
        if not segments:
            return []

        blocks = []
        block_start = 0

        for i in range(len(segments) - 1):
            gap = segments[i + 1].start - segments[i].end

            # If gap is significant (>3 seconds), end this block
            if gap > 3.0:
                blocks.append((block_start, i))
                block_start = i + 1

        # Add the final block
        blocks.append((block_start, len(segments) - 1))

        return blocks

    def _get_block_text(self, segments: list[Segment], start_idx: int, end_idx: int) -> str:
        """Get combined text of a block of segments."""
        return " ".join(seg.text for seg in segments[start_idx:end_idx + 1])

    def _find_retake_blocks(self, segments: list[Segment]) -> list[list[tuple[int, int]]]:
        """
        Find groups of recording blocks that are retakes of each other.

        This works by:
        1. Identifying continuous recording blocks (separated by pauses)
        2. Comparing the start of each block to find similar beginnings
        3. Grouping blocks that start similarly (indicating a restart/retake)

        Returns:
            List of groups, where each group is a list of (start_idx, end_idx) block tuples
        """
        blocks = self._find_recording_blocks(segments)

        if len(blocks) < 2:
            return []

        # For each block, get the first ~50 characters as fingerprint
        block_fingerprints: list[tuple[tuple[int, int], str]] = []
        for block in blocks:
            start_idx, end_idx = block
            # Get first segment's text as fingerprint
            first_text = segments[start_idx].text.lower()
            # Also include second segment if available for better matching
            if end_idx > start_idx:
                first_text += " " + segments[start_idx + 1].text.lower()
            block_fingerprints.append((block, first_text[:100]))

        # Group blocks by similar fingerprints
        retake_groups: list[list[tuple[int, int]]] = []
        used_blocks: set[int] = set()

        for i, (block_i, fp_i) in enumerate(block_fingerprints):
            if i in used_blocks:
                continue

            group = [block_i]
            used_blocks.add(i)

            for j, (block_j, fp_j) in enumerate(block_fingerprints[i + 1:], start=i + 1):
                if j in used_blocks:
                    continue

                # Check if fingerprints are similar
                similarity = fuzz.ratio(fp_i, fp_j) / 100.0
                partial_sim = fuzz.partial_ratio(fp_i, fp_j) / 100.0

                if similarity >= 0.6 or partial_sim >= 0.75:
                    group.append(block_j)
                    used_blocks.add(j)

            if len(group) > 1:
                retake_groups.append(group)

        return retake_groups

    def detect_retakes(self, segments: list[Segment]) -> list[RetakeGroup]:
        """
        Detect segments that are retakes of each other using block-based analysis.

        This improved algorithm:
        1. Identifies recording blocks (continuous speech separated by pauses)
        2. Compares block beginnings to detect retakes
        3. Keeps the best block from each retake group

        Args:
            segments: List of transcribed segments

        Returns:
            List of retake groups
        """
        retake_groups: list[RetakeGroup] = []
        used_indices: set[int] = set()
        group_id = 0

        # Strategy 1: Block-based retake detection
        retake_blocks = self._find_retake_blocks(segments)

        for block_group in retake_blocks:
            group = RetakeGroup(id=group_id)

            for block_start, block_end in block_group:
                # Create a virtual segment representing the entire block
                block_text = self._get_block_text(segments, block_start, block_end)
                block_start_time = segments[block_start].start
                block_end_time = segments[block_end].end

                virtual_segment = Segment(
                    start=block_start_time,
                    end=block_end_time,
                    text=block_text,
                    confidence=1.0
                )

                group.segments.append(AnalyzedSegment(
                    segment=virtual_segment,
                    action=SegmentAction.RETAKE_CANDIDATE,
                    retake_group_id=group_id
                ))

                # Mark all segments in this block as used
                for idx in range(block_start, block_end + 1):
                    used_indices.add(idx)

            if len(group.segments) > 1:
                retake_groups.append(group)
                group_id += 1

        # Strategy 2: Detect within-block restarts (short false starts)
        # These are segments that start similarly but are quickly corrected
        for i, seg1 in enumerate(segments):
            if i in used_indices:
                continue

            # Check next few segments for a restart pattern
            for j in range(i + 1, min(i + 4, len(segments))):
                if j in used_indices:
                    continue

                seg2 = segments[j]

                # Only check nearby segments (within 10 seconds)
                if seg2.start - seg1.end > 10.0:
                    break

                # Check if they start similarly (prefix matching)
                prefix1 = self._get_segment_prefix(seg1.text, min_words=4)
                prefix2 = self._get_segment_prefix(seg2.text, min_words=4)

                if len(prefix1) >= 10 and len(prefix2) >= 10:
                    prefix_similarity = fuzz.ratio(prefix1, prefix2) / 100.0

                    if prefix_similarity >= 0.7:
                        # This is a within-block restart - keep the longer/later one
                        group = RetakeGroup(id=group_id)
                        group.segments.append(AnalyzedSegment(
                            segment=seg1,
                            action=SegmentAction.RETAKE_CANDIDATE,
                            retake_group_id=group_id
                        ))
                        group.segments.append(AnalyzedSegment(
                            segment=seg2,
                            action=SegmentAction.RETAKE_CANDIDATE,
                            retake_group_id=group_id
                        ))
                        used_indices.add(i)
                        used_indices.add(j)
                        retake_groups.append(group)
                        group_id += 1
                        break

        # Strategy 3: Individual segment similarity for remaining segments
        # This catches cases where just a single segment was retaken
        for i, seg1 in enumerate(segments):
            if i in used_indices:
                continue

            group = RetakeGroup(id=group_id)
            group.segments.append(AnalyzedSegment(
                segment=seg1,
                action=SegmentAction.RETAKE_CANDIDATE,
                retake_group_id=group_id
            ))
            used_indices.add(i)

            # Look for similar segments within 60 seconds
            for j in range(i + 1, len(segments)):
                if j in used_indices:
                    continue

                seg2 = segments[j]

                # Stop looking if we're too far in time
                if seg2.start - seg1.end > 60.0:
                    break

                similarity = fuzz.ratio(seg1.text.lower(), seg2.text.lower()) / 100.0

                if similarity >= self.config.retake_similarity:
                    group.segments.append(AnalyzedSegment(
                        segment=seg2,
                        action=SegmentAction.RETAKE_CANDIDATE,
                        retake_group_id=group_id
                    ))
                    used_indices.add(j)

            if len(group.segments) > 1:
                retake_groups.append(group)
                group_id += 1

        console.print(f"[yellow]Found {len(retake_groups)} retake groups[/yellow]")
        return retake_groups
    
    def detect_filler_words(self, segments: list[Segment]) -> list[int]:
        """
        Detect segments with excessive filler words.
        
        Args:
            segments: List of transcribed segments
            
        Returns:
            Indices of segments with excessive fillers
        """
        filler_indices = []
        
        for i, seg in enumerate(segments):
            text_lower = seg.text.lower()
            filler_count = sum(1 for filler in self.config.filler_words if filler in text_lower)
            word_count = len(seg.text.split())
            
            # Flag if more than 30% of words are fillers
            if word_count > 0 and filler_count / word_count > 0.3:
                filler_indices.append(i)
        
        if filler_indices:
            console.print(f"[yellow]Found {len(filler_indices)} segments with excessive fillers[/yellow]")
        
        return filler_indices
    
    def select_best_take_llm(self, group: RetakeGroup) -> int:
        """
        Use LLM to select the best take from a retake group.
        
        Args:
            group: RetakeGroup with multiple takes
            
        Returns:
            Index of the best take within the group
        """
        # Build prompt
        segments_text = "\n".join(
            f"{i + 1}. \"{seg.segment.text}\" (duration: {seg.segment.duration:.1f}s)"
            for i, seg in enumerate(group.segments)
        )
        
        prompt = f"""You are analyzing multiple takes of the same spoken content in Hungarian.
Select the best take based on:
1. Completeness of the thought
2. Natural flow and clarity
3. Absence of hesitation or filler words
4. Proper pronunciation and delivery

Here are the takes:
{segments_text}

Respond with ONLY the number (1, 2, 3, etc.) of the best take. Nothing else."""


        # Try Gemini first (primary)
        if self._gemini_client:
            try:
                response = self._gemini_client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=prompt
                )
                answer = response.text.strip()
                best_index = int(answer) - 1  # Convert to 0-indexed
                
                if 0 <= best_index < len(group.segments):
                    return best_index
            except Exception as e:
                console.print(f"[yellow]Gemini failed: {e}. Trying fallback...[/yellow]")
        
        # Try OpenAI as fallback
        if self._openai_client:
            try:
                response = self._openai_client.chat.completions.create(
                    model=self.config.llm_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=10,
                    temperature=0
                )
                
                answer = response.choices[0].message.content.strip()
                best_index = int(answer) - 1  # Convert to 0-indexed
                
                if 0 <= best_index < len(group.segments):
                    return best_index
            except Exception as e:
                console.print(f"[red]OpenAI fallback failed: {e}. Using duration-based selection.[/red]")
        
        # Final fallback: pick the longest take
        return max(range(len(group.segments)), 
                  key=lambda i: group.segments[i].segment.duration)
    
    def select_best_takes(self, groups: list[RetakeGroup]) -> list[RetakeGroup]:
        """
        Select the best take from each retake group.
        
        Args:
            groups: List of retake groups
            
        Returns:
            Groups with best_index set
        """
        for group in groups:
            if len(group.segments) == 1:
                group.best_index = 0
            else:
                group.best_index = self.select_best_take_llm(group)
                console.print(
                    f"[green]Retake group {group.id}: selected take {group.best_index + 1} "
                    f"of {len(group.segments)}[/green]"
                )
        
        return groups
    
    def analyze(self, segments: list[Segment], video_duration: float) -> tuple[list[TimeRange], list[Segment]]:
        """
        Full analysis pipeline.

        Args:
            segments: Transcribed segments
            video_duration: Total video duration

        Returns:
            Tuple of (segments to keep as TimeRanges, cleaned segments for captions)
        """
        console.print("[blue]Analyzing segments...[/blue]")

        # Detect silences
        silences = self.detect_silences(segments, video_duration)

        # Detect retakes
        retake_groups = self.detect_retakes(segments)

        # Select best takes
        retake_groups = self.select_best_takes(retake_groups)

        # Collect time ranges to remove (non-best takes)
        ranges_to_remove: list[TimeRange] = []

        for group in retake_groups:
            for i, analyzed in enumerate(group.segments):
                if i != group.best_index:
                    # This segment (or block) should be removed
                    ranges_to_remove.append(TimeRange(
                        analyzed.segment.start,
                        analyzed.segment.end
                    ))

        # Build keep ranges by checking each segment against removal ranges
        keep_ranges: list[TimeRange] = []
        kept_segments: list[Segment] = []
        removed_count = 0

        for seg in segments:
            # Check if this segment falls within a removal range
            should_remove = False
            for remove_range in ranges_to_remove:
                # Segment is within removal range if it overlaps significantly
                if seg.start >= remove_range.start and seg.end <= remove_range.end:
                    should_remove = True
                    break

            if not should_remove:
                # Apply buffers to prevent word cutoff
                buffered_start = max(0.0, seg.start - self.config.segment_start_buffer)
                buffered_end = min(video_duration, seg.end + self.config.segment_end_buffer)
                keep_ranges.append(TimeRange(buffered_start, buffered_end))
                kept_segments.append(seg)
            else:
                removed_count += 1

        # Merge overlapping ranges that may have been created by buffering
        keep_ranges, kept_segments = self._merge_overlapping_ranges(keep_ranges, kept_segments)

        console.print(f"[green]✓[/green] Keeping {len(keep_ranges)} segments, removing {removed_count} bad takes")

        return keep_ranges, kept_segments

    def _merge_overlapping_ranges(
        self,
        ranges: list[TimeRange],
        segments: list[Segment]
    ) -> tuple[list[TimeRange], list[Segment]]:
        """
        Merge overlapping time ranges and their corresponding segments.

        When buffers are applied, adjacent ranges may overlap. This merges them
        to avoid duplicate content in the output video.
        """
        if not ranges:
            return [], []

        # Sort by start time
        paired = sorted(zip(ranges, segments), key=lambda x: x[0].start)

        merged_ranges = []
        merged_segments = []

        current_range, current_seg = paired[0]

        for next_range, next_seg in paired[1:]:
            # Check if ranges overlap or are adjacent (within 0.05s)
            if next_range.start <= current_range.end + 0.05:
                # Merge: extend current range to include next
                current_range = TimeRange(
                    current_range.start,
                    max(current_range.end, next_range.end)
                )
                # Merge segment text
                current_seg = Segment(
                    start=current_seg.start,
                    end=max(current_seg.end, next_seg.end),
                    text=current_seg.text + " " + next_seg.text,
                    confidence=min(current_seg.confidence, next_seg.confidence),
                    tokens=None  # Tokens would need complex merging
                )
            else:
                # No overlap, save current and start new
                merged_ranges.append(current_range)
                merged_segments.append(current_seg)
                current_range = next_range
                current_seg = next_seg

        # Don't forget the last one
        merged_ranges.append(current_range)
        merged_segments.append(current_seg)

        return merged_ranges, merged_segments
