"""Data models for GUI state management."""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..transcriber import Segment, Token
from ..analyzer import AnalyzedSegment, TimeRange, SegmentAction


@dataclass
class CropConfig:
    """Configuration for video cropping and panning.

    All coordinates are normalized (0.0-1.0) relative to video dimensions.
    The crop region is defined by its center position and size.
    Pan offsets allow scrolling content within the crop frame.
    """
    # Crop region size (normalized 0.0-1.0)
    width: float = 1.0   # Crop width (1.0 = full width)
    height: float = 1.0  # Crop height (1.0 = full height)

    # Pan offset - position of video content within crop frame
    # 0.0 = centered, negative = shifted left/up, positive = shifted right/down
    pan_x: float = 0.0
    pan_y: float = 0.0

    @property
    def is_default(self) -> bool:
        """Check if crop is at default (no cropping/panning)."""
        return (self.width == 1.0 and self.height == 1.0 and
                self.pan_x == 0.0 and self.pan_y == 0.0)

    def get_crop_rect(self, video_width: int, video_height: int) -> tuple[int, int, int, int]:
        """Calculate the actual crop rectangle in pixels.

        Returns:
            (x, y, width, height) in pixels for FFmpeg crop filter
        """
        crop_w = int(self.width * video_width)
        crop_h = int(self.height * video_height)

        # Available space for panning
        max_pan_x = video_width - crop_w
        max_pan_y = video_height - crop_h

        # Calculate position based on pan offset (-1 to 1 maps to full range)
        # pan_x=0 means centered, pan_x=-1 means left edge, pan_x=1 means right edge
        crop_x = int((max_pan_x / 2) * (1 + self.pan_x)) if max_pan_x > 0 else 0
        crop_y = int((max_pan_y / 2) * (1 + self.pan_y)) if max_pan_y > 0 else 0

        # Clamp to valid range
        crop_x = max(0, min(crop_x, video_width - crop_w))
        crop_y = max(0, min(crop_y, video_height - crop_h))

        return crop_x, crop_y, crop_w, crop_h

    def to_ffmpeg_filter(self, video_width: int, video_height: int) -> str:
        """Generate FFmpeg crop filter string.

        Args:
            video_width: Source video width in pixels
            video_height: Source video height in pixels

        Returns:
            FFmpeg crop filter string, e.g., "crop=1280:720:320:180"
        """
        x, y, w, h = self.get_crop_rect(video_width, video_height)
        return f"crop={w}:{h}:{x}:{y}"

    def to_dict(self) -> dict:
        """Serialize for JSON storage."""
        return {
            "width": self.width,
            "height": self.height,
            "pan_x": self.pan_x,
            "pan_y": self.pan_y
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CropConfig":
        """Deserialize from JSON."""
        return cls(
            width=data.get("width", 1.0),
            height=data.get("height", 1.0),
            pan_x=data.get("pan_x", 0.0),
            pan_y=data.get("pan_y", 0.0)
        )

    def copy(self) -> "CropConfig":
        """Create a copy of this config."""
        return CropConfig(
            width=self.width,
            height=self.height,
            pan_x=self.pan_x,
            pan_y=self.pan_y
        )


@dataclass
class CaptionSettings:
    """Configuration for caption display and styling.

    Position is stored as normalized coordinates (0.0-1.0) relative to video dimensions.
    The position represents the center-x and bottom-y of the caption box.
    Box dimensions are also normalized (0.0-1.0) relative to video dimensions.
    """
    font_size: int = 24
    font_family: str = "Arial"
    show_preview: bool = True

    # Normalized position (0.0-1.0) - center_x, bottom_y of caption box
    pos_x: float = 0.5  # Centered horizontally
    pos_y: float = 0.92  # Near bottom (92% down from top)

    # Box dimensions as fraction of video dimensions (0.0-1.0)
    # Default: 60% width, ~7% height (good for 2 lines at 24pt on 1080p)
    box_width: float = 0.6
    box_height: float = 0.07

    # Styling options
    show_background: bool = True  # Show semi-transparent background behind text
    text_color: str = "white"  # Text color: "white" or "black"
    font_weight: str = "bold"  # Font weight: "regular", "medium", "semi-bold", "bold", "extra-bold"

    def to_dict(self) -> dict:
        """Serialize for JSON storage."""
        return {
            "font_size": self.font_size,
            "font_family": self.font_family,
            "show_preview": self.show_preview,
            "pos_x": self.pos_x,
            "pos_y": self.pos_y,
            "box_width": self.box_width,
            "box_height": self.box_height,
            "show_background": self.show_background,
            "text_color": self.text_color,
            "font_weight": self.font_weight
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CaptionSettings":
        """Deserialize from JSON."""
        # Handle legacy format with position/vertical_offset
        if "position" in data and "pos_x" not in data:
            # Convert legacy format
            position = data.get("position", "bottom")
            offset = data.get("vertical_offset", 60.0)
            # Approximate conversion (assuming 1080p)
            if position == "top":
                pos_y = offset / 1080.0 + 0.07
            elif position == "center":
                pos_y = 0.5
            else:  # bottom
                pos_y = 1.0 - offset / 1080.0
            return cls(
                font_size=data.get("font_size", 24),
                font_family=data.get("font_family", "Arial"),
                show_preview=data.get("show_preview", True),
                pos_x=0.5,
                pos_y=pos_y,
                box_width=0.6,
                box_height=0.07
            )

        return cls(
            font_size=data.get("font_size", 24),
            font_family=data.get("font_family", "Arial"),
            show_preview=data.get("show_preview", True),
            pos_x=data.get("pos_x", 0.5),
            pos_y=data.get("pos_y", 0.92),
            box_width=data.get("box_width", 0.6),
            box_height=data.get("box_height", 0.07),
            show_background=data.get("show_background", True),
            text_color=data.get("text_color", "white"),
            font_weight=data.get("font_weight", "bold")
        )

    def copy(self) -> "CaptionSettings":
        """Create a copy of this settings."""
        return CaptionSettings(
            font_size=self.font_size,
            font_family=self.font_family,
            show_preview=self.show_preview,
            pos_x=self.pos_x,
            pos_y=self.pos_y,
            box_width=self.box_width,
            box_height=self.box_height,
            show_background=self.show_background,
            text_color=self.text_color,
            font_weight=self.font_weight
        )

    def get_box_pixels(self, video_width: int, video_height: int) -> tuple[float, float, float, float]:
        """Calculate the caption box rectangle in pixels.

        Args:
            video_width: Video width in pixels
            video_height: Video height in pixels

        Returns:
            (x, y, width, height) pixel coordinates for the caption box
        """
        # Calculate box dimensions in pixels
        width = self.box_width * video_width
        height = self.box_height * video_height

        # pos_x is center of caption, pos_y is bottom of caption
        center_x = self.pos_x * video_width
        bottom_y = self.pos_y * video_height

        # Calculate top-left corner
        x = center_x - width / 2
        y = bottom_y - height

        return x, y, width, height

    def get_pixel_position(self, video_width: int, video_height: int, text_width: float, text_height: float) -> tuple[float, float]:
        """Calculate pixel position for the caption box top-left corner.

        Args:
            video_width: Video width in pixels
            video_height: Video height in pixels
            text_width: Caption text width in pixels (ignored, uses box_width)
            text_height: Caption text height in pixels (ignored, uses box_height)

        Returns:
            (x, y) pixel coordinates for the top-left corner of the caption box
        """
        x, y, _, _ = self.get_box_pixels(video_width, video_height)
        return x, y


@dataclass
class RecordingConfig:
    """Configuration for screen and audio recording.

    Stores settings for the recorder tab including screen selection,
    aspect ratio cropping, audio input, and output quality.
    """
    # Screen capture settings
    screen_index: int = 0  # Which screen to capture (for multi-monitor)
    capture_full_screen: bool = True  # True for full screen, False for aspect ratio crop

    # Aspect ratio crop settings (used when capture_full_screen is False)
    # Common ratios: (16, 9) landscape, (9, 16) portrait, (1, 1) square, (4, 3) standard
    target_aspect_ratio: tuple[int, int] | None = None

    # Fixed resolution crop settings (takes precedence over aspect ratio)
    # e.g., (1920, 1080) for 1080p, (1280, 720) for 720p
    # If resolution exceeds screen size, it will be scaled down proportionally
    target_resolution: tuple[int, int] | None = None

    # Crop region position (normalized 0.0-1.0)
    # Represents where the crop region is positioned on the screen
    crop_offset_x: float = 0.5  # 0.0 = left edge, 0.5 = center, 1.0 = right edge
    crop_offset_y: float = 0.5  # 0.0 = top edge, 0.5 = center, 1.0 = bottom edge

    # Audio settings
    audio_device_id: str = ""  # Empty string = system default
    audio_enabled: bool = True
    audio_volume: float = 1.0  # 0.0-1.0

    # Output settings
    output_directory: str = ""  # Empty = user's Videos folder
    filename_pattern: str = "recording_{timestamp}"
    video_quality: str = "high"  # "low", "medium", "high", "very_high"
    audio_sample_rate: int = 48000  # Professional quality
    container_format: str = "mp4"

    def get_crop_rect(self, screen_width: int, screen_height: int, margin: int = 50) -> tuple[int, int, int, int]:
        """Calculate the crop rectangle in pixels for FFmpeg post-processing.

        Args:
            screen_width: Screen width in pixels
            screen_height: Screen height in pixels
            margin: Extra pixels to capture on each side (default 50px).
                    This compensates for coordinate system mismatches between
                    the preview overlay and the actual screen capture.

        Returns:
            (x, y, width, height) in pixels
        """
        if self.capture_full_screen:
            return (0, 0, screen_width, screen_height)

        # Fixed resolution mode (takes precedence over aspect ratio)
        if self.target_resolution is not None:
            req_w, req_h = self.target_resolution

            # Scale down proportionally if larger than screen
            scale = min(1.0, screen_width / req_w, screen_height / req_h)
            crop_width = int(req_w * scale)
            crop_height = int(req_h * scale)

        # Aspect ratio mode
        elif self.target_aspect_ratio is not None:
            target_w, target_h = self.target_aspect_ratio
            target_ratio = target_w / target_h
            screen_ratio = screen_width / screen_height

            if target_ratio > screen_ratio:
                # Target is wider than screen - use full width, crop height
                crop_width = screen_width
                crop_height = int(screen_width / target_ratio)
            else:
                # Target is taller than screen - use full height, crop width
                crop_height = screen_height
                crop_width = int(screen_height * target_ratio)
        else:
            # No crop specified
            return (0, 0, screen_width, screen_height)

        # Calculate position based on offset
        max_x = screen_width - crop_width
        max_y = screen_height - crop_height
        crop_x = int(max_x * self.crop_offset_x)
        crop_y = int(max_y * self.crop_offset_y)

        # Apply margin - expand the crop area to capture more than selected
        # This compensates for preview-to-recording coordinate mismatches
        if margin > 0:
            # Expand crop area
            crop_x = max(0, crop_x - margin)
            crop_y = max(0, crop_y - margin)
            crop_width = min(screen_width - crop_x, crop_width + 2 * margin)
            crop_height = min(screen_height - crop_y, crop_height + 2 * margin)

        return (crop_x, crop_y, crop_width, crop_height)

    def to_ffmpeg_crop_filter(self, screen_width: int, screen_height: int) -> str | None:
        """Generate FFmpeg crop filter string if cropping is needed.

        Returns:
            FFmpeg crop filter string, e.g., "crop=1920:1080:320:0", or None if no crop
        """
        if self.capture_full_screen:
            return None
        if self.target_resolution is None and self.target_aspect_ratio is None:
            return None

        x, y, w, h = self.get_crop_rect(screen_width, screen_height)
        return f"crop={w}:{h}:{x}:{y}"

    def to_dict(self) -> dict:
        """Serialize for JSON storage."""
        return {
            "screen_index": self.screen_index,
            "capture_full_screen": self.capture_full_screen,
            "target_aspect_ratio": list(self.target_aspect_ratio) if self.target_aspect_ratio else None,
            "target_resolution": list(self.target_resolution) if self.target_resolution else None,
            "crop_offset_x": self.crop_offset_x,
            "crop_offset_y": self.crop_offset_y,
            "audio_device_id": self.audio_device_id,
            "audio_enabled": self.audio_enabled,
            "audio_volume": self.audio_volume,
            "output_directory": self.output_directory,
            "filename_pattern": self.filename_pattern,
            "video_quality": self.video_quality,
            "audio_sample_rate": self.audio_sample_rate,
            "container_format": self.container_format,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RecordingConfig":
        """Deserialize from JSON."""
        aspect = data.get("target_aspect_ratio")
        resolution = data.get("target_resolution")
        return cls(
            screen_index=data.get("screen_index", 0),
            capture_full_screen=data.get("capture_full_screen", True),
            target_aspect_ratio=tuple(aspect) if aspect else None,
            target_resolution=tuple(resolution) if resolution else None,
            crop_offset_x=data.get("crop_offset_x", 0.5),
            crop_offset_y=data.get("crop_offset_y", 0.5),
            audio_device_id=data.get("audio_device_id", ""),
            audio_enabled=data.get("audio_enabled", True),
            audio_volume=data.get("audio_volume", 1.0),
            output_directory=data.get("output_directory", ""),
            filename_pattern=data.get("filename_pattern", "recording_{timestamp}"),
            video_quality=data.get("video_quality", "high"),
            audio_sample_rate=data.get("audio_sample_rate", 48000),
            container_format=data.get("container_format", "mp4"),
        )

    def copy(self) -> "RecordingConfig":
        """Create a copy of this config."""
        return RecordingConfig(
            screen_index=self.screen_index,
            capture_full_screen=self.capture_full_screen,
            target_aspect_ratio=self.target_aspect_ratio,
            target_resolution=self.target_resolution,
            crop_offset_x=self.crop_offset_x,
            crop_offset_y=self.crop_offset_y,
            audio_device_id=self.audio_device_id,
            audio_enabled=self.audio_enabled,
            audio_volume=self.audio_volume,
            output_directory=self.output_directory,
            filename_pattern=self.filename_pattern,
            video_quality=self.video_quality,
            audio_sample_rate=self.audio_sample_rate,
            container_format=self.container_format,
        )


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
    - Crop configuration (global and per-segment overrides)
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

    # Crop configuration
    crop_config: CropConfig = field(default_factory=CropConfig)  # Global crop settings
    segment_crop_overrides: dict[int, CropConfig] = field(default_factory=dict)  # Per-segment overrides

    # Caption settings
    caption_settings: CaptionSettings = field(default_factory=CaptionSettings)

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

    # Crop configuration methods

    def set_global_crop(self, config: CropConfig) -> None:
        """Set the global crop configuration."""
        self.crop_config = config

    def get_segment_crop(self, index: int) -> CropConfig:
        """Get the crop config for a segment (override or global)."""
        if index in self.segment_crop_overrides:
            return self.segment_crop_overrides[index]
        return self.crop_config

    def set_segment_crop(self, index: int, config: CropConfig) -> None:
        """Set a crop override for a specific segment."""
        if 0 <= index < len(self.original_segments):
            self.segment_crop_overrides[index] = config

    def clear_segment_crop(self, index: int) -> None:
        """Remove crop override for a segment (reverts to global)."""
        self.segment_crop_overrides.pop(index, None)

    def has_segment_crop_override(self, index: int) -> bool:
        """Check if a segment has a crop override."""
        return index in self.segment_crop_overrides

    def reset_all_crops(self) -> None:
        """Reset all crop settings to default."""
        self.crop_config = CropConfig()
        self.segment_crop_overrides.clear()

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
            "version": "1.1",
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
            ],
            "crop_config": self.crop_config.to_dict() if not self.crop_config.is_default else None,
            "segment_crop_overrides": {
                str(k): v.to_dict() for k, v in self.segment_crop_overrides.items()
            } if self.segment_crop_overrides else None,
            "caption_settings": self.caption_settings.to_dict()
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

        # Load crop configuration (v1.1+)
        crop_data = data.get("crop_config")
        crop_config = CropConfig.from_dict(crop_data) if crop_data else CropConfig()

        # Load per-segment crop overrides
        segment_crops_data = data.get("segment_crop_overrides", {})
        segment_crop_overrides = {
            int(k): CropConfig.from_dict(v)
            for k, v in segment_crops_data.items()
        } if segment_crops_data else {}

        # Load caption settings
        caption_data = data.get("caption_settings")
        caption_settings = CaptionSettings.from_dict(caption_data) if caption_data else CaptionSettings()

        session = cls(
            video_path=Path(data["video_path"]),
            video_duration=data["video_duration"],
            original_segments=segments,
            analyzed_segments=analyzed,
            tokens=tokens,
            text_edits={int(k): v for k, v in data.get("text_edits", {}).items()},
            keep_overrides={int(k): v for k, v in data.get("keep_overrides", {}).items()},
            highlight_regions=highlights,
            crop_config=crop_config,
            segment_crop_overrides=segment_crop_overrides,
            caption_settings=caption_settings
        )

        return session

    def has_unsaved_changes(self) -> bool:
        """Check if there are unsaved user modifications."""
        return (bool(self.text_edits) or bool(self.keep_overrides) or
                bool(self.highlight_regions) or not self.crop_config.is_default or
                bool(self.segment_crop_overrides))
