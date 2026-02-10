"""Video cutting module using FFmpeg."""

import subprocess
import tempfile
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn

from .analyzer import TimeRange
from .config import Config
from .encoder import get_encoder_args, EncoderConfig

console = Console()


class Cutter:
    """Handles video cutting and concatenation using FFmpeg."""

    # Gap between segments in seconds
    SEGMENT_GAP = 0.2

    def __init__(self, config: Config, encoder_config: EncoderConfig | None = None):
        self.config = config
        self.encoder_config = encoder_config or EncoderConfig()
    
    def get_video_duration(self, video_path: Path) -> float:
        """
        Get the duration of a video file using FFprobe.

        Args:
            video_path: Path to the video file

        Returns:
            Duration in seconds
        """
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"FFprobe failed: {result.stderr}")

        return float(result.stdout.strip())

    def get_video_dimensions(self, video_path: Path) -> tuple[int, int]:
        """
        Get video width and height using FFprobe.

        Args:
            video_path: Path to the video file

        Returns:
            Tuple of (width, height) in pixels
        """
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(video_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"FFprobe failed: {result.stderr}")

        parts = result.stdout.strip().split(",")
        width = int(parts[0]) if len(parts) > 0 else 1920
        height = int(parts[1]) if len(parts) > 1 else 1080

        return width, height
    
    def cut_segment(
        self,
        input_path: Path,
        output_path: Path,
        start: float,
        end: float,
        index: int,
        freeze_last_frame: bool = True,
        crop_filter: str | None = None
    ) -> Path:
        """
        Extract a single segment from the video with precise timing.

        Uses re-encoding to ensure frame-accurate cuts. Stream copy (-c copy)
        can only cut at keyframes, causing timing mismatches that desync captions.

        Optionally freezes the last frame for SEGMENT_GAP duration to create
        smooth transitions between segments (uses two-pass for reliability).

        Args:
            input_path: Path to input video
            output_path: Path for output segment
            start: Start time in seconds
            end: End time in seconds
            index: Segment index for logging
            freeze_last_frame: Whether to freeze last frame for gap duration
            crop_filter: Optional FFmpeg crop filter string (e.g., "crop=1280:720:320:180")

        Returns:
            Path to the extracted segment
        """
        # Single-pass approach using filter chains for both trimming and padding
        # This avoids double re-encoding which degrades audio quality
        if freeze_last_frame and self.SEGMENT_GAP > 0:
            # Build video filter chain: trim + optional crop + tpad
            duration = end - start
            vf_parts = [f"trim=start={start}:duration={duration}", "setpts=PTS-STARTPTS"]
            if crop_filter:
                vf_parts.append(crop_filter)
            vf_parts.append(f"tpad=stop_mode=clone:stop_duration={self.SEGMENT_GAP}")
            video_filter = ",".join(vf_parts)

            # Build audio filter chain: trim + pad
            af_parts = [f"atrim=start={start}:duration={duration}", "asetpts=PTS-STARTPTS"]
            af_parts.append(f"apad=pad_dur={self.SEGMENT_GAP}")
            audio_filter = ",".join(af_parts)

            encoder_args = get_encoder_args(self.encoder_config)
            cmd = [
                "ffmpeg",
                "-y",
                "-i", str(input_path),
                "-vf", video_filter,
                "-af", audio_filter,
                *encoder_args,
                "-c:a", "aac",
                "-b:a", "256k",
                "-shortest",
                "-avoid_negative_ts", "make_zero",
                str(output_path)
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg segment extraction failed: {result.stderr}")

        else:
            # Single pass without frame freezing
            cmd = [
                "ffmpeg",
                "-y",
                "-ss", str(start),
                "-i", str(input_path),
                "-t", str(end - start),
            ]

            # Add crop filter if specified
            if crop_filter:
                cmd.extend(["-vf", crop_filter])

            encoder_args = get_encoder_args(self.encoder_config)
            cmd.extend([
                *encoder_args,
                "-c:a", "aac",
                "-b:a", "256k",
                "-avoid_negative_ts", "make_zero",
                str(output_path)
            ])

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg segment extraction failed: {result.stderr}")

        return output_path

    def create_gap_segment(
        self,
        reference_video: Path,
        output_path: Path,
        duration: float
    ) -> Path:
        """
        Create a short black video segment with silence for gaps between segments.

        Args:
            reference_video: Reference video to match resolution/fps
            output_path: Path for the gap segment
            duration: Duration of the gap in seconds

        Returns:
            Path to the gap segment
        """
        # Get video properties from reference
        probe_cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-of", "csv=p=0",
            str(reference_video)
        ]
        result = subprocess.run(probe_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # Fallback to common values
            width, height, fps = 1920, 1080, 30
        else:
            parts = result.stdout.strip().split(",")
            width = int(parts[0]) if len(parts) > 0 else 1920
            height = int(parts[1]) if len(parts) > 1 else 1080
            # Parse fps (might be "30/1" format)
            fps_str = parts[2] if len(parts) > 2 else "30"
            if "/" in fps_str:
                num, den = fps_str.split("/")
                fps = int(num) / int(den)
            else:
                fps = float(fps_str)

        # Create black video with silent audio
        encoder_args = get_encoder_args(self.encoder_config)
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:r={fps}:d={duration}",
            "-f", "lavfi",
            "-i", f"anullsrc=r=48000:cl=stereo:d={duration}",
            *encoder_args,
            "-c:a", "aac",
            "-shortest",
            str(output_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg gap segment creation failed: {result.stderr}")

        return output_path

    def concatenate_segments(
        self,
        segment_paths: list[Path],
        output_path: Path
    ) -> Path:
        """
        Concatenate multiple video segments into one.

        Each segment already has frozen last frame appended (via cut_segment),
        so no separate gap segments are needed.

        Args:
            segment_paths: List of paths to segment files
            output_path: Path for the concatenated output

        Returns:
            Path to the concatenated video
        """
        temp_dir = self.config.temp_dir or Path(tempfile.gettempdir())
        concat_file = temp_dir / "concat_list.txt"

        # Create concat file - segments already have frozen frames appended
        with open(concat_file, "w") as f:
            for seg_path in segment_paths:
                f.write(f"file '{seg_path}'\n")

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(output_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        # Clean up concat file
        if not self.config.keep_temp:
            concat_file.unlink()

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg concatenation failed: {result.stderr}")

        return output_path

    def cut_video(
        self,
        input_path: Path,
        ranges: list[TimeRange],
        output_path: Path,
        crop_filter: str | None = None,
        segment_crop_filters: dict[int, str] | None = None
    ) -> Path:
        """
        Cut and concatenate video based on time ranges.

        Args:
            input_path: Path to input video
            ranges: List of time ranges to keep
            output_path: Path for final output
            crop_filter: Global crop filter to apply to all segments (e.g., "crop=1280:720:320:180")
            segment_crop_filters: Per-segment crop filter overrides {segment_index: filter_string}

        Returns:
            Path to the processed video
        """
        input_path = Path(input_path)
        output_path = Path(output_path)

        if not ranges:
            raise ValueError("No segments to keep")

        console.print(f"[blue]Cutting {len(ranges)} segments...[/blue]")
        if crop_filter:
            console.print(f"[blue]Applying crop: {crop_filter}[/blue]")

        # Setup temp directory
        temp_dir = self.config.temp_dir or Path(tempfile.gettempdir())
        temp_dir = temp_dir / f"video_editor_{input_path.stem}"
        temp_dir.mkdir(exist_ok=True)

        segment_paths: list[Path] = []
        segment_crop_filters = segment_crop_filters or {}

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Extracting segments", total=len(ranges))

            for i, range_ in enumerate(ranges):
                seg_path = temp_dir / f"segment_{i:04d}.mp4"
                # Don't freeze last frame on the final segment (no transition after it)
                is_last = (i == len(ranges) - 1)

                # Use per-segment crop if available, otherwise use global crop
                segment_crop = segment_crop_filters.get(i, crop_filter)

                self.cut_segment(
                    input_path, seg_path, range_.start, range_.end, i,
                    freeze_last_frame=not is_last,
                    crop_filter=segment_crop
                )
                segment_paths.append(seg_path)
                progress.update(task, advance=1)

        # Concatenate all segments
        console.print("[blue]Concatenating segments...[/blue]")

        if len(segment_paths) == 1:
            # Just copy the single segment
            import shutil
            shutil.copy(segment_paths[0], output_path)
        else:
            self.concatenate_segments(segment_paths, output_path)

        # Clean up temp segments
        if not self.config.keep_temp:
            for seg_path in segment_paths:
                if seg_path.exists():
                    seg_path.unlink()
            if temp_dir.exists():
                try:
                    temp_dir.rmdir()
                except OSError:
                    pass  # Directory not empty, leave it

        console.print(f"[green]✓[/green] Video saved to {output_path}")
        return output_path
