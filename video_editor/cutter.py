"""Video cutting module using FFmpeg."""

import subprocess
import tempfile
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn

from .analyzer import TimeRange
from .config import Config

console = Console()


class Cutter:
    """Handles video cutting and concatenation using FFmpeg."""
    
    def __init__(self, config: Config):
        self.config = config
    
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
    
    def cut_segment(
        self,
        input_path: Path,
        output_path: Path,
        start: float,
        end: float,
        index: int
    ) -> Path:
        """
        Extract a single segment from the video with precise timing.

        Uses re-encoding to ensure frame-accurate cuts. Stream copy (-c copy)
        can only cut at keyframes, causing timing mismatches that desync captions.

        Args:
            input_path: Path to input video
            output_path: Path for output segment
            start: Start time in seconds
            end: End time in seconds
            index: Segment index for logging

        Returns:
            Path to the extracted segment
        """
        # Use -ss before -i for fast seeking to approximate position,
        # then re-encode to get precise frame-accurate cuts
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(start),          # Seek before input (fast approximate seek)
            "-i", str(input_path),
            "-t", str(end - start),     # Duration
            "-c:v", "libx264",          # Re-encode video for precise cuts
            "-preset", "fast",          # Balance speed vs compression
            "-crf", "18",               # High quality (lower = better, 18 is visually lossless)
            "-c:a", "aac",              # Re-encode audio
            "-b:a", "192k",             # Audio bitrate
            "-avoid_negative_ts", "make_zero",
            str(output_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg segment extraction failed: {result.stderr}")

        return output_path
    
    def concatenate_segments(
        self,
        segment_paths: list[Path],
        output_path: Path
    ) -> Path:
        """
        Concatenate multiple video segments into one.
        
        Args:
            segment_paths: List of paths to segment files
            output_path: Path for the concatenated output
            
        Returns:
            Path to the concatenated video
        """
        # Create concat file
        temp_dir = self.config.temp_dir or Path(tempfile.gettempdir())
        concat_file = temp_dir / "concat_list.txt"
        
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
        output_path: Path
    ) -> Path:
        """
        Cut and concatenate video based on time ranges.
        
        Args:
            input_path: Path to input video
            ranges: List of time ranges to keep
            output_path: Path for final output
            
        Returns:
            Path to the processed video
        """
        input_path = Path(input_path)
        output_path = Path(output_path)
        
        if not ranges:
            raise ValueError("No segments to keep")
        
        console.print(f"[blue]Cutting {len(ranges)} segments...[/blue]")
        
        # Setup temp directory
        temp_dir = self.config.temp_dir or Path(tempfile.gettempdir())
        temp_dir = temp_dir / f"video_editor_{input_path.stem}"
        temp_dir.mkdir(exist_ok=True)
        
        segment_paths: list[Path] = []
        
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
                self.cut_segment(input_path, seg_path, range_.start, range_.end, i)
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
