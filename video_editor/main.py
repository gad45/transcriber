"""CLI entry point for the video editing agent."""

import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # Load .env file automatically

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import Config, CaptionStyle
from .transcriber import Transcriber, Token
from .analyzer import Analyzer, TimeRange
from .cutter import Cutter
from .captioner import Captioner
from .qc import QualityController

console = Console()


def _adjust_tokens_for_cuts(tokens: list[Token], keep_ranges: list[TimeRange], segment_gap: float = 0.2) -> list[Token]:
    """
    Adjust token timestamps for the cut video timeline.

    Uses a single-pass algorithm where each token is processed exactly once.
    This prevents the duplication bug where nested loops would add the same
    token multiple times with different offsets.

    Args:
        tokens: Original tokens with timestamps from the full video
        keep_ranges: List of time ranges that are being kept
        segment_gap: Gap duration between segments (default: 0.2s)

    Returns:
        List of tokens with adjusted timestamps for the cut video
    """
    if not tokens or not keep_ranges:
        return []

    # Sort tokens and ranges by start time
    sorted_tokens = sorted(tokens, key=lambda t: t.start)
    sorted_ranges = sorted(keep_ranges, key=lambda r: r.start)

    # Precompute cumulative offsets for each range
    # offsets[i] = total duration of all ranges before range i + gaps between them
    offsets = []
    cumulative = 0.0
    for i, range_ in enumerate(sorted_ranges):
        offsets.append(cumulative)
        cumulative += range_.duration
        # Add gap after each segment except the last
        if i < len(sorted_ranges) - 1:
            cumulative += segment_gap

    adjusted_tokens = []
    range_idx = 0

    for token in sorted_tokens:
        # Skip ranges that end before this token starts
        while range_idx < len(sorted_ranges) and sorted_ranges[range_idx].end <= token.start:
            range_idx += 1

        # Check if token falls within current range
        if range_idx < len(sorted_ranges):
            range_ = sorted_ranges[range_idx]
            if range_.start <= token.start < range_.end:
                # Token is within this range - adjust timestamp
                new_start = offsets[range_idx] + (token.start - range_.start)
                new_end = offsets[range_idx] + (token.end - range_.start)

                # Clamp end time to not exceed the range's contribution
                new_end = min(new_end, offsets[range_idx] + range_.duration)

                adjusted_tokens.append(Token(
                    text=token.text,
                    start=new_start,
                    end=new_end
                ))

    console.print(f"[dim]Token adjustment: {len(tokens)} → {len(adjusted_tokens)} tokens[/dim]")
    return adjusted_tokens


def print_banner():
    """Print the application banner."""
    console.print(Panel.fit(
        "[bold blue]AI Video Editing Agent[/bold blue]\n"
        "[dim]Automatic bad take removal & captioning for Hungarian content[/dim]",
        border_style="blue"
    ))


def print_preview(ranges: list[TimeRange], segments: list, original_duration: float):
    """Print a preview of proposed cuts."""
    table = Table(title="Proposed Segments to Keep")
    table.add_column("Segment", style="cyan")
    table.add_column("Start", style="green")
    table.add_column("End", style="green")
    table.add_column("Duration", style="yellow")
    table.add_column("Text Preview", style="white", max_width=50)
    
    total_kept = 0
    for i, (range_, seg) in enumerate(zip(ranges, segments), 1):
        duration = range_.end - range_.start
        total_kept += duration
        text_preview = seg.text[:47] + "..." if len(seg.text) > 50 else seg.text
        table.add_row(
            str(i),
            f"{range_.start:.1f}s",
            f"{range_.end:.1f}s",
            f"{duration:.1f}s",
            text_preview
        )
    
    console.print(table)
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Original duration: {original_duration:.1f}s")
    console.print(f"  Kept duration: {total_kept:.1f}s")
    console.print(f"  Removed: {original_duration - total_kept:.1f}s ({(1 - total_kept/original_duration)*100:.1f}%)")


@click.command()
@click.argument("input_video", type=click.Path(exists=True))
@click.option("-o", "--output", "output_path", type=click.Path(),
              help="Output video path (default: input_edited.mp4)")
@click.option("--silence-threshold", type=float, default=1.5,
              help="Minimum silence duration to cut (seconds)")
@click.option("--retake-similarity", type=float, default=0.8,
              help="Similarity threshold for retake detection (0-1)")
@click.option("--caption-style",
              type=click.Choice(["minimal", "modern", "bold"]),
              default="modern", help="Caption style")
@click.option("--no-captions", is_flag=True, help="Skip caption generation")
@click.option("--soft-captions", is_flag=True,
              help="Add soft subtitles instead of burning")
@click.option("--streaming-captions", is_flag=True,
              help="Enable word-by-word streaming captions (burned in)")
@click.option("--max-words", type=int, default=15,
              help="Maximum words on screen for streaming captions (default: 15)")
@click.option("--preview", is_flag=True,
              help="Show proposed cuts without processing")
@click.option("--keep-temp", is_flag=True,
              help="Keep temporary files for debugging")
@click.option("--openai-key", envvar="OPENAI_API_KEY",
              help="OpenAI API key for LLM take selection")
@click.option("--skip-qc", is_flag=True,
              help="Skip transcription quality control")
@click.option("--qc-report-only", is_flag=True,
              help="Run QC but don't auto-correct (just report issues)")
def main(
    input_video: str,
    output_path: str | None,
    silence_threshold: float,
    retake_similarity: float,
    caption_style: str,
    no_captions: bool,
    soft_captions: bool,
    streaming_captions: bool,
    max_words: int,
    preview: bool,
    keep_temp: bool,
    openai_key: str | None,
    skip_qc: bool,
    qc_report_only: bool
):
    """
    AI Video Editing Agent - Automatically edit Hungarian spoken content.
    
    This tool transcribes Hungarian speech, removes bad takes and silences,
    and adds captions to your video.
    """
    print_banner()
    
    input_path = Path(input_video)
    
    # Determine output path
    if output_path is None:
        output_path = input_path.parent / f"{input_path.stem}_edited{input_path.suffix}"
    else:
        output_path = Path(output_path)
    
    # Create config
    config = Config(
        silence_threshold=silence_threshold,
        retake_similarity=retake_similarity,
        caption_style=CaptionStyle(caption_style),
        streaming_captions=streaming_captions,
        max_caption_words=max_words,
        keep_temp=keep_temp,
        openai_api_key=openai_key,
        temp_dir=Path(tempfile.gettempdir()) / "video_editor"
    )
    
    # Ensure temp dir exists
    if config.temp_dir:
        config.temp_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Initialize components
        transcriber = Transcriber(config)
        analyzer = Analyzer(config)
        cutter = Cutter(config)
        captioner = Captioner(config)
        
        # Determine total steps based on flags
        # Base: video info, transcribe, analyze, process = 4
        # +1 for QC
        total_steps = 4
        if not skip_qc:
            total_steps += 1

        # Step 1: Get video duration
        console.print(f"\n[bold]Step 1/{total_steps}:[/bold] Getting video info...")
        video_duration = cutter.get_video_duration(input_path)
        console.print(f"  Video duration: {video_duration:.1f}s")

        # Step 2: Transcribe
        console.print(f"\n[bold]Step 2/{total_steps}:[/bold] Transcribing speech...")
        segments, tokens = transcriber.transcribe_video(input_path)

        if not segments:
            console.print("[red]No speech detected in video![/red]")
            sys.exit(1)

        # Track current step number
        current_step = 3

        # Step 3: Quality Control (optional)
        if not skip_qc:
            console.print(f"\n[bold]Step {current_step}/{total_steps}:[/bold] Running transcription quality control...")
            qc = QualityController(config, auto_correct=not qc_report_only)

            if qc.is_available():
                qc_report = qc.check_segments(segments)

                # Apply corrections if enabled
                if not qc_report_only:
                    segments = qc.apply_corrections(segments, qc_report)
            current_step += 1
        else:
            console.print(f"\n[dim]Step {current_step}/{total_steps}: Quality control skipped[/dim]")


        # Analyze for bad takes and silences
        console.print(f"\n[bold]Step {current_step}/{total_steps}:[/bold] Analyzing for bad takes and silences...")
        keep_ranges, kept_segments = analyzer.analyze(segments, video_duration)
        
        # Preview mode - just show what would be cut
        if preview:
            console.print("\n[bold yellow]PREVIEW MODE[/bold yellow] - No changes will be made\n")
            print_preview(keep_ranges, kept_segments, video_duration)
            console.print("\n[dim]Run without --preview to process the video.[/dim]")
            return

        # Process video step
        current_step += 1
        console.print(f"\n[bold]Step {current_step}/{total_steps}:[/bold] Processing video...")
        
        # Cut video
        temp_cut = config.temp_dir / f"{input_path.stem}_cut.mp4" if config.temp_dir else Path(tempfile.gettempdir()) / f"{input_path.stem}_cut.mp4"
        
        if no_captions:
            # Save directly to output
            cutter.cut_video(input_path, keep_ranges, output_path)
        else:
            # Cut to temp, then add captions
            cutter.cut_video(input_path, keep_ranges, temp_cut)

            if streaming_captions:
                # Use word-by-word streaming captions
                # Adjust token times for the cut video (accounting for gaps between segments)
                adjusted_tokens = _adjust_tokens_for_cuts(tokens, keep_ranges, Cutter.SEGMENT_GAP)

                captioner.burn_streaming_captions(
                    temp_cut,
                    adjusted_tokens,
                    output_path,
                    max_words=config.max_caption_words
                )
            else:
                # Use traditional segment-based captions
                # Adjust segment times for the cut video
                adjusted_segments = []
                current_time = 0.0
                for seg in kept_segments:
                    duration = seg.duration
                    from .transcriber import Segment
                    adjusted_seg = Segment(
                        start=current_time,
                        end=current_time + duration,
                        text=seg.text,
                        confidence=seg.confidence
                    )
                    adjusted_segments.append(adjusted_seg)
                    current_time += duration

                # Generate SRT
                srt_path = config.temp_dir / f"{input_path.stem}.srt" if config.temp_dir else Path(tempfile.gettempdir()) / f"{input_path.stem}.srt"
                captioner.generate_srt(adjusted_segments, srt_path)

                # Add captions
                if soft_captions:
                    captioner.add_soft_captions(temp_cut, srt_path, output_path)
                else:
                    captioner.burn_captions(temp_cut, srt_path, output_path)

                # Clean up SRT
                if not keep_temp and srt_path.exists():
                    srt_path.unlink()

            # Clean up temp cut file
            if not keep_temp and temp_cut.exists():
                temp_cut.unlink()

        # Final summary
        new_duration = cutter.get_video_duration(output_path)
        console.print(Panel.fit(
            f"[bold green]✓ Processing complete![/bold green]\n\n"
            f"Output: [cyan]{output_path}[/cyan]\n"
            f"Original: {video_duration:.1f}s → Final: {new_duration:.1f}s\n"
            f"Removed: {video_duration - new_duration:.1f}s ({(1 - new_duration/video_duration)*100:.1f}%)",
            title="Summary",
            border_style="green"
        ))
        
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}")
        if keep_temp:
            console.print("[dim]Temporary files preserved for debugging.[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    main()
