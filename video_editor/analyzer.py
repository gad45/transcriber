"""Analysis module for detecting bad takes, pauses, and retakes."""

import os
import re
from dataclasses import dataclass, field
from enum import Enum

from rapidfuzz import fuzz
from rich.console import Console

from .config import Config
from .transcriber import Segment

console = Console()

# Hungarian-specific speech quality markers
HUNGARIAN_HESITATION_MARKERS = {
    # Filled pauses
    "öö": "filled_pause",
    "ööö": "filled_pause",
    "öööö": "filled_pause",
    "hmm": "filled_pause",
    "hm": "filled_pause",
    "aha": "filled_pause",
    "ő": "filled_pause",
    # Hedge words (when used as fillers)
    "szóval": "hedge",
    "tehát": "hedge",
    "hát": "hedge",
    "ugye": "hedge",
    # Placeholder words (like English "um, thing")
    "izé": "placeholder",
    "izélni": "placeholder",
    "hogyishívják": "placeholder",
    # Restart signals
    "na": "restart_signal",
    "nos": "restart_signal",
    "oké": "restart_signal",
    # Self-correction markers
    "nem": "correction_signal",
    "azaz": "correction_signal",
    "vagyis": "correction_signal",
    "helyesebben": "correction_signal",
    "bocsánat": "correction_signal",
    "várj": "correction_signal",
}

# Patterns indicating incomplete sentences
HUNGARIAN_INCOMPLETE_PATTERNS = [
    r"\bés\s*$",      # ends with "és" (and)
    r"\bde\s*$",      # ends with "de" (but)
    r"\bhogy\s*$",    # ends with "hogy" (that)
    r"\bami\s*$",     # ends with "ami" (which)
    r"\bakkor\s*$",   # ends with "akkor" (then)
    r"\bmert\s*$",    # ends with "mert" (because)
    r"\bvagy\s*$",    # ends with "vagy" (or)
    r"\bha\s*$",      # ends with "ha" (if)
    r"\bmint\s*$",    # ends with "mint" (like/as)
    r"\bahol\s*$",    # ends with "ahol" (where)
]

# Patterns indicating complete sentences
HUNGARIAN_COMPLETION_MARKERS = [
    r"\.\s*$",        # proper sentence ending
    r"!\s*$",         # exclamation
    r"\?\s*$",        # question
    r"köszönöm\s*$",  # "thank you"
    r"rendben\s*$",   # "alright"
    r"ennyi\s*$",     # "that's it"
    r"vége\s*$",      # "the end"
]

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


@dataclass
class TakeMetrics:
    """Objective metrics computed for a single take."""
    word_count: int
    duration: float
    hesitation_count: int
    hesitation_types: dict[str, int]
    incomplete_sentence: bool
    has_completion_marker: bool
    correction_signals: int
    position: int  # chronological order (0-indexed)


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
    
    def _compute_take_metrics(self, segment: Segment, position: int) -> TakeMetrics:
        """
        Compute objective metrics for a single take.

        Args:
            segment: The segment to analyze
            position: Chronological position (0-indexed)

        Returns:
            TakeMetrics with computed values
        """
        text = segment.text.lower()
        words = text.split()

        # Count hesitation markers by type
        hesitation_types: dict[str, int] = {}
        hesitation_count = 0
        correction_signals = 0

        for marker, marker_type in HUNGARIAN_HESITATION_MARKERS.items():
            # Use word boundary matching for more accurate detection
            pattern = r'\b' + re.escape(marker) + r'\b'
            matches = len(re.findall(pattern, text))
            if matches > 0:
                hesitation_types[marker_type] = hesitation_types.get(marker_type, 0) + matches
                if marker_type == "correction_signal":
                    correction_signals += matches
                else:
                    hesitation_count += matches

        # Check for incomplete sentence patterns
        incomplete_sentence = any(
            re.search(pattern, text) for pattern in HUNGARIAN_INCOMPLETE_PATTERNS
        )

        # Check for completion markers
        has_completion_marker = any(
            re.search(pattern, text) for pattern in HUNGARIAN_COMPLETION_MARKERS
        )

        return TakeMetrics(
            word_count=len(words),
            duration=segment.duration,
            hesitation_count=hesitation_count,
            hesitation_types=hesitation_types,
            incomplete_sentence=incomplete_sentence,
            has_completion_marker=has_completion_marker,
            correction_signals=correction_signals,
            position=position
        )

    def _format_take_with_metrics(self, segment: Segment, metrics: TakeMetrics, is_last: bool) -> str:
        """Format a take with its metrics for the LLM prompt."""
        position_label = "final attempt - most recent" if is_last else f"attempt {metrics.position + 1}"

        # Build indicators list
        indicators = []
        if metrics.hesitation_count > 0:
            indicators.append(f"{metrics.hesitation_count} hesitation(s)")
        if metrics.correction_signals > 0:
            indicators.append(f"{metrics.correction_signals} self-correction(s)")
        if metrics.incomplete_sentence:
            indicators.append("incomplete sentence")
        if metrics.has_completion_marker:
            indicators.append("complete sentence")

        indicators_str = ", ".join(indicators) if indicators else "no issues detected"

        return (
            f"Take {metrics.position + 1} ({position_label}):\n"
            f"Text: \"{segment.text}\"\n"
            f"Duration: {metrics.duration:.1f}s | Indicators: {indicators_str}"
        )

    def _build_enhanced_prompt(self, group: RetakeGroup, metrics_list: list[TakeMetrics]) -> str:
        """Build the enhanced LLM prompt with metrics and context."""
        num_takes = len(group.segments)

        # Format each take with metrics
        takes_text = "\n\n".join(
            self._format_take_with_metrics(
                seg.segment,
                metrics,
                is_last=(i == num_takes - 1)
            )
            for i, (seg, metrics) in enumerate(zip(group.segments, metrics_list))
        )

        prompt = f"""You are a Hungarian speech quality analyst evaluating multiple takes.

## RECORDING CONTEXT
Takes are in CHRONOLOGICAL ORDER (1=first, {num_takes}=most recent).
The speaker re-recorded to improve. Later takes are usually the speaker's preferred version.

## HUNGARIAN SPEECH QUALITY INDICATORS
**Hesitation markers**: "öö", "hmm", "hát", "izé" (indicate uncertainty)
**Self-correction**: "nem, ...", "azaz", "vagyis" (speaker correcting themselves)
**Incomplete**: ends with conjunctions (és, de, hogy, mert) without completing thought
**Restart signals**: "na", "nos" at beginning often indicate fresh attempt

## TAKES TO EVALUATE
{takes_text}

## EVALUATION INSTRUCTIONS
For each take, consider:
1. COMPLETENESS: Does it express a complete thought?
2. FLUENCY: Natural flow without excessive hesitation?
3. SELF-CORRECTIONS: Mid-sentence corrections present?
4. INTENT: Signs of being abandoned (trailing off)?

IMPORTANT: Prefer the LAST take unless it has a clear problem (incomplete, more hesitations than earlier takes, obvious mistake).

## RESPONSE FORMAT
REASONING: [One sentence explaining your choice. If NOT selecting the last take, explain what problem it has.]
DECISION: [number]"""

        return prompt

    def _parse_structured_response(self, response: str, num_takes: int) -> tuple[int, str]:
        """
        Parse the structured LLM response.

        Returns:
            Tuple of (selected_index, reasoning)
        """
        lines = response.strip().split('\n')

        decision = None
        reasoning = ""

        for line in lines:
            line_stripped = line.strip()

            if line_stripped.upper().startswith("DECISION:"):
                try:
                    # Extract number from "DECISION: 3" or "DECISION:3"
                    num_str = line_stripped.split(":", 1)[1].strip()
                    # Handle cases like "3 (final take)" by taking first number
                    num_match = re.search(r'\d+', num_str)
                    if num_match:
                        decision = int(num_match.group()) - 1  # Convert to 0-indexed
                except (ValueError, IndexError):
                    pass
            elif line_stripped.upper().startswith("REASONING:"):
                reasoning = line_stripped.split(":", 1)[1].strip() if ":" in line_stripped else ""

        # Validate decision
        if decision is None or not (0 <= decision < num_takes):
            decision = num_takes - 1  # Default to last take
            reasoning = "Fallback: defaulting to last take"

        return decision, reasoning

    def _validate_decision(self, decision: int, metrics_list: list[TakeMetrics]) -> tuple[int, str | None]:
        """
        Apply validation rules to catch obvious LLM errors.

        Returns:
            Tuple of (validated_decision, override_reason or None)
        """
        if not metrics_list:
            return decision, None

        selected = metrics_list[decision]
        last = metrics_list[-1]
        last_index = len(metrics_list) - 1

        # Rule 1: If selecting non-last take, verify the last take actually has issues
        if decision != last_index:
            # Check if last take is objectively better
            if (last.has_completion_marker and
                not selected.has_completion_marker and
                last.hesitation_count <= selected.hesitation_count):
                return last_index, "last take is complete with fewer/equal hesitations"

        # Rule 2: Never select an incomplete sentence if complete alternatives exist
        if selected.incomplete_sentence:
            # Prefer last complete take
            for i in range(len(metrics_list) - 1, -1, -1):
                if not metrics_list[i].incomplete_sentence:
                    return i, "avoiding incomplete take"

        return decision, None

    def _fallback_selection(self, metrics_list: list[TakeMetrics]) -> int:
        """
        Fallback selection when LLM unavailable.
        Selects the last complete take.
        """
        # Find the last take that is complete
        for i in range(len(metrics_list) - 1, -1, -1):
            if metrics_list[i].has_completion_marker and not metrics_list[i].incomplete_sentence:
                return i

        # If none explicitly complete, return the last one
        return len(metrics_list) - 1

    def select_best_take_llm(self, group: RetakeGroup) -> tuple[int, str]:
        """
        Use LLM to select the best take from a retake group with enhanced understanding.

        Args:
            group: RetakeGroup with multiple takes

        Returns:
            Tuple of (index of best take, reason for selection)
        """
        num_takes = len(group.segments)

        # Stage 1: Compute metrics for all takes
        metrics_list = [
            self._compute_take_metrics(seg.segment, i)
            for i, seg in enumerate(group.segments)
        ]

        # Stage 2: Build enhanced prompt and query LLM
        prompt = self._build_enhanced_prompt(group, metrics_list)

        decision = None
        reasoning = ""

        # Try Gemini first (primary)
        if self._gemini_client:
            try:
                response = self._gemini_client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=prompt
                )
                decision, reasoning = self._parse_structured_response(response.text, num_takes)
            except Exception as e:
                console.print(f"[yellow]Gemini failed: {e}. Trying fallback...[/yellow]")

        # Try OpenAI as fallback
        if decision is None and self._openai_client:
            try:
                response = self._openai_client.chat.completions.create(
                    model=self.config.llm_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=200,
                    temperature=0
                )
                answer = response.choices[0].message.content
                decision, reasoning = self._parse_structured_response(answer, num_takes)
            except Exception as e:
                console.print(f"[yellow]OpenAI failed: {e}. Using rule-based selection.[/yellow]")

        # Stage 3: Validate decision or use fallback
        if decision is None:
            decision = self._fallback_selection(metrics_list)
            reasoning = "rule-based: selected last complete take"
        else:
            validated_decision, override_reason = self._validate_decision(decision, metrics_list)
            if override_reason:
                decision = validated_decision
                reasoning = f"override: {override_reason}"

        return decision, reasoning
    
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
                best_index, reasoning = self.select_best_take_llm(group)
                group.best_index = best_index

                # Summary output (1 line per group)
                num_takes = len(group.segments)
                is_last = (best_index == num_takes - 1)

                if is_last:
                    console.print(
                        f"[blue]Retake group {group.id}: selected take {best_index + 1} of {num_takes} "
                        f"({reasoning})[/blue]"
                    )
                else:
                    console.print(
                        f"[yellow]Retake group {group.id}: selected take {best_index + 1} of {num_takes} "
                        f"({reasoning})[/yellow]"
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
