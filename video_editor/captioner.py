"""Caption generation and burning module."""

from pathlib import Path
import subprocess
import re

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .config import Config, CaptionStyle, CAPTION_STYLES
from .transcriber import Segment, Token
from .encoder import get_encoder_args, EncoderConfig

console = Console()


class Captioner:
    """Handles SRT generation and caption burning."""

    def __init__(self, config: Config, encoder_config: EncoderConfig | None = None):
        self.config = config
        self.encoder_config = encoder_config or EncoderConfig()
    
    def generate_srt(self, segments: list[Segment], output_path: Path) -> Path:
        """
        Generate an SRT subtitle file from segments.
        
        Args:
            segments: List of transcribed segments
            output_path: Path for the SRT file
            
        Returns:
            Path to the generated SRT file
        """
        output_path = Path(output_path)
        
        with open(output_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(segments, 1):
                f.write(seg.to_srt_entry(i))
                f.write("\n")
        
        console.print(f"[green]✓[/green] Generated captions: {output_path}")
        return output_path
    
    def _build_style_string(self) -> str:
        """Build the ASS style string for captions."""
        style_config = CAPTION_STYLES.get(self.config.caption_style, CAPTION_STYLES[CaptionStyle.MODERN])
        
        # Override font size if specified in config
        style_config = style_config.copy()
        style_config["FontSize"] = self.config.caption_font_size
        style_config["FontName"] = self.config.caption_font
        
        # Build force_style string
        parts = [f"{key}={value}" for key, value in style_config.items()]
        return ",".join(parts)
    
    def burn_captions(
        self,
        video_path: Path,
        srt_path: Path,
        output_path: Path
    ) -> Path:
        """
        Burn captions into video using FFmpeg.

        Note: Requires FFmpeg built with libass. Falls back to soft captions if unavailable.

        Args:
            video_path: Path to input video
            srt_path: Path to SRT file
            output_path: Path for output video with burned captions

        Returns:
            Path to the output video
        """
        import shutil
        import os

        video_path = Path(video_path).resolve()
        srt_path = Path(srt_path).resolve()
        output_path = Path(output_path).resolve()

        # Check if subtitles filter is available (requires libass)
        check_result = subprocess.run(
            ["ffmpeg", "-filters"],
            capture_output=True,
            text=True
        )

        if "subtitles" not in check_result.stdout:
            console.print("[yellow]Warning: FFmpeg not built with libass. Using soft captions instead.[/yellow]")
            return self.add_soft_captions(video_path, srt_path, output_path)

        console.print("[blue]Burning captions into video...[/blue]")

        # Copy SRT to output directory with simple name to avoid path escaping issues
        output_dir = output_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        temp_srt = output_dir / "captions_temp.srt"
        shutil.copy(srt_path, temp_srt)

        # Build the subtitles filter
        subtitle_filter = "subtitles=captions_temp.srt"

        # Change to output directory to use simple relative path for SRT
        original_cwd = os.getcwd()
        os.chdir(output_dir)

        try:
            encoder_args = get_encoder_args(self.encoder_config)
            cmd = [
                "ffmpeg",
                "-y",
                "-i", str(video_path),
                "-vf", subtitle_filter,
                *encoder_args,
                "-c:a", "copy",
                str(output_path)
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg caption burning failed: {result.stderr}")

            console.print(f"[green]✓[/green] Video with captions saved to {output_path}")
            return output_path
        finally:
            os.chdir(original_cwd)
            if temp_srt.exists() and not self.config.keep_temp:
                temp_srt.unlink()
    
    def add_soft_captions(
        self,
        video_path: Path,
        srt_path: Path,
        output_path: Path
    ) -> Path:
        """
        Add soft subtitles (selectable) to video.
        
        Args:
            video_path: Path to input video
            srt_path: Path to SRT file
            output_path: Path for output video
            
        Returns:
            Path to the output video
        """
        video_path = Path(video_path)
        srt_path = Path(srt_path)
        output_path = Path(output_path)
        
        console.print("[blue]Adding soft captions to video...[/blue]")
        
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(video_path),
            "-i", str(srt_path),
            "-c", "copy",
            "-c:s", "mov_text",
            "-metadata:s:s:0", "language=hun",
            str(output_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg soft subtitle addition failed: {result.stderr}")
        
        console.print(f"[green]✓[/green] Video with soft captions saved to {output_path}")
        return output_path

    def _chunk_tokens(self, tokens: list[Token], max_words: int = 20, gap_threshold: float = 1.5) -> list[list[Token]]:
        """
        Group tokens into display chunks based on max words and silence gaps.

        Args:
            tokens: List of word-level tokens
            max_words: Maximum words per chunk
            gap_threshold: Silence gap (seconds) that triggers a new chunk

        Returns:
            List of token chunks
        """
        if not tokens:
            return []

        chunks = []
        current_chunk: list[Token] = []

        for i, token in enumerate(tokens):
            current_chunk.append(token)

            # Check if we should end this chunk
            should_end_chunk = False

            # Max words reached
            if len(current_chunk) >= max_words:
                should_end_chunk = True

            # Check for silence gap to next token
            if i < len(tokens) - 1:
                gap = tokens[i + 1].start - token.end
                if gap > gap_threshold:
                    should_end_chunk = True

            # Last token
            if i == len(tokens) - 1:
                should_end_chunk = True

            if should_end_chunk and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []

        return chunks

    def _escape_drawtext(self, text: str) -> str:
        """Escape special characters for FFmpeg drawtext filter."""
        # Escape backslash first, then other special chars
        text = text.replace("\\", "\\\\")
        text = text.replace("'", "\\'")
        text = text.replace(":", "\\:")
        text = text.replace("%", "\\%")
        return text

    def _ensure_punctuation_spacing(self, text: str) -> str:
        """Ensure there's a space after sentence-ending punctuation.

        Fixes cases where tokens are concatenated without proper spacing,
        e.g., "Hello.World" becomes "Hello. World".
        """
        # Add space after . ! ? if followed directly by a letter (handles Latin + accented chars)
        text = re.sub(r'([.!?])([A-Za-zÀ-ÿ])', r'\1 \2', text)
        return text

    def _split_into_lines(self, text: str, words_per_line: int = 8) -> list[str]:
        """
        Split text into multiple lines for better readability.

        Args:
            text: The text to split
            words_per_line: Target words per line

        Returns:
            List of lines (max 2 lines)
        """
        words = text.split()
        if len(words) <= words_per_line:
            return [text]

        line1 = " ".join(words[:words_per_line])
        line2 = " ".join(words[words_per_line:words_per_line * 2])

        if line2:
            return [line1, line2]
        return [line1]

    def _build_drawtext_filter(self, tokens: list[Token], max_words: int = 15, caption_settings: dict = None) -> str:
        """
        Build FFmpeg drawtext filter chain for streaming captions.

        Creates accumulating text effect: words appear one by one and stay visible
        until the chunk is complete, then clear for the next chunk.

        Uses separate drawtext filters for each line to avoid newline rendering issues.

        Args:
            tokens: List of word-level tokens with timing
            max_words: Maximum words per display chunk (default: 15)
            caption_settings: Optional dict with GUI caption settings

        Returns:
            FFmpeg filter string
        """
        chunks = self._chunk_tokens(tokens, max_words)

        if not chunks:
            return ""

        filters = []

        # Style settings - use GUI settings if provided, else fall back to config
        if caption_settings:
            fontsize = caption_settings.get("font_size", self.config.caption_font_size)
            fontname = caption_settings.get("font_family", "Arial")
            text_color = caption_settings.get("text_color", "white")
            show_background = caption_settings.get("show_background", True)
            font_weight = caption_settings.get("font_weight", "bold")
            pos_x = caption_settings.get("pos_x", 0.5)
            pos_y = caption_settings.get("pos_y", 0.92)
            box_width = caption_settings.get("box_width", 0.6)
        else:
            fontsize = self.config.caption_font_size
            fontname = "Arial"
            text_color = "white"
            show_background = True
            font_weight = "bold"
            pos_x = 0.5
            pos_y = 0.92
            box_width = 0.6

        fontcolor = text_color
        borderw = 3
        bordercolor = "white" if text_color == "black" else "black"

        # Map font weight to FFmpeg font style suffix
        # FFmpeg drawtext uses font family with style, e.g., "Roboto:style=SemiBold"
        # Different fonts use different naming conventions (SemiBold vs Semibold, ExtraBold vs Heavy)
        weight_style_map = {
            "regular": "Regular",
            "medium": "Medium",
            "semi-bold": "Semibold",  # Use lowercase 'b' for broader compatibility (Lato uses this)
            "bold": "Bold",
            "extra-bold": "Black",  # Most fonts use "Black" for extra-bold weight
        }
        font_style = weight_style_map.get(font_weight, "Bold")

        # Construct font specification with style
        if font_style and font_style != "Regular":
            font_spec = f"{fontname}\\:style={font_style}"
        else:
            font_spec = fontname

        # Box settings based on show_background
        if show_background:
            box = 1
            boxcolor = "black@0.7" if text_color == "white" else "white@0.7"
            boxborderw = 10
        else:
            box = 0
            boxcolor = "black@0.0"
            boxborderw = 0

        # Calculate position - pos_y is the bottom of the caption box (0.0 = top, 1.0 = bottom)
        # We need to calculate the Y position for the text
        # The caption box height is roughly 2 lines of text
        line_height = fontsize + 10  # Approximate line height

        # For bottom position: y = (pos_y * h) - line_height for line 2, - 2*line_height for line 1
        # pos_y=0.92 means the bottom of the caption is at 92% of video height
        line2_y = f"h*{pos_y}-{line_height}"
        line1_y = f"h*{pos_y}-{line_height * 2}"

        # X position - centered based on pos_x
        x_expr = f"w*{pos_x}-text_w/2"

        for chunk in chunks:
            chunk_end = chunk[-1].end + 0.1  # Small buffer after last word

            # For each word position in the chunk, create filters that show
            # all words from the start up to that word
            for word_idx in range(len(chunk)):
                # Accumulate text from start of chunk to current word
                accumulated_text = "".join(t.text for t in chunk[:word_idx + 1]).strip()
                accumulated_text = self._ensure_punctuation_spacing(accumulated_text)

                # Split into 2 lines for readability (roughly half the words per line)
                words_per_line = max(4, (max_words + 1) // 2)  # e.g., 15 words -> 8 per line
                lines = self._split_into_lines(accumulated_text, words_per_line=words_per_line)

                # This filter is active from when this word starts until the next word starts
                # (or until chunk end for the last word)
                # Apply caption delay so captions appear slightly after the word is spoken
                delay = self.config.caption_delay
                word_start = chunk[word_idx].start + delay

                if word_idx < len(chunk) - 1:
                    word_end = chunk[word_idx + 1].start + delay
                else:
                    word_end = chunk_end + delay

                # Build separate drawtext filter for each line
                # Line 1 (always present)
                escaped_line1 = self._escape_drawtext(lines[0])
                filter_str1 = (
                    f"drawtext=text='{escaped_line1}'"
                    f":font='{font_spec}'"
                    f":fontsize={fontsize}"
                    f":fontcolor={fontcolor}"
                    f":borderw={borderw}"
                    f":bordercolor={bordercolor}"
                    f":box={box}"
                    f":boxcolor={boxcolor}"
                    f":boxborderw={boxborderw}"
                    f":x={x_expr}"
                    f":y={line1_y}"
                    f":enable='between(t,{word_start:.3f},{word_end:.3f})'"
                )
                filters.append(filter_str1)

                # Line 2 (only if there's a second line)
                if len(lines) > 1 and lines[1]:
                    escaped_line2 = self._escape_drawtext(lines[1])
                    filter_str2 = (
                        f"drawtext=text='{escaped_line2}'"
                        f":font='{font_spec}'"
                        f":fontsize={fontsize}"
                        f":fontcolor={fontcolor}"
                        f":borderw={borderw}"
                        f":bordercolor={bordercolor}"
                        f":box={box}"
                        f":boxcolor={boxcolor}"
                        f":boxborderw={boxborderw}"
                        f":x={x_expr}"
                        f":y={line2_y}"
                        f":enable='between(t,{word_start:.3f},{word_end:.3f})'"
                    )
                    filters.append(filter_str2)

        return ",".join(filters)

    def _check_ffmpeg_filter(self, filter_name: str) -> bool:
        """Check if an FFmpeg filter is available."""
        result = subprocess.run(
            ["ffmpeg", "-filters"],
            capture_output=True,
            text=True
        )
        return filter_name in result.stdout

    def _generate_streaming_ass(self, tokens: list[Token], output_path: Path, max_words: int = 20) -> Path:
        """
        Generate an ASS subtitle file with streaming word-by-word captions.

        Uses ASS format with karaoke-style timing for word-by-word reveal.

        Args:
            tokens: List of word-level tokens
            output_path: Path for the ASS file
            max_words: Maximum words per display chunk

        Returns:
            Path to the generated ASS file
        """
        chunks = self._chunk_tokens(tokens, max_words)

        # ASS header with style definition
        ass_content = """[Script Info]
Title: Streaming Captions
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,3,3,1,2,20,20,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        def format_ass_time(seconds: float) -> str:
            """Convert seconds to ASS timestamp format (H:MM:SS.CC)."""
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            centis = int((seconds % 1) * 100)
            return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"

        # Generate dialogue lines for each chunk
        for chunk in chunks:
            if not chunk:
                continue

            chunk_start = chunk[0].start
            chunk_end = chunk[-1].end + 0.1

            # Build the text with karaoke tags
            # Each word gets a \k tag with its duration in centiseconds
            karaoke_text = ""
            for i, token in enumerate(chunk):
                # Duration from this word start to next word start (or chunk end)
                if i < len(chunk) - 1:
                    duration_cs = int((chunk[i + 1].start - token.start) * 100)
                else:
                    duration_cs = int((token.end - token.start) * 100)

                # Use \kf for progressive fill effect
                karaoke_text += f"{{\\kf{duration_cs}}}{token.text}"

            # Write the dialogue line
            start_time = format_ass_time(chunk_start)
            end_time = format_ass_time(chunk_end)
            ass_content += f"Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,{karaoke_text}\n"

        output_path = Path(output_path)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(ass_content)

        return output_path

    def burn_streaming_captions(
        self,
        video_path: Path,
        tokens: list[Token],
        output_path: Path,
        max_words: int = 20,
        caption_settings: dict = None
    ) -> Path:
        """
        Burn streaming captions into video using FFmpeg.

        Tries multiple approaches in order of preference:
        1. drawtext filter (requires libfreetype)
        2. ASS subtitles with karaoke effect (requires libass)
        3. Falls back to regular SRT-based captions

        Args:
            video_path: Path to input video
            tokens: List of word-level tokens with timing
            output_path: Path for output video
            max_words: Maximum words on screen at once (default: 20)
            caption_settings: Optional dict with GUI caption settings
                (font_size, font_family, font_weight, text_color, show_background,
                 pos_x, pos_y, box_width, box_height)

        Returns:
            Path to the output video
        """
        import shutil
        import os

        video_path = Path(video_path).resolve()
        output_path = Path(output_path).resolve()

        console.print(f"[blue]Burning streaming captions ({len(tokens)} words, max {max_words} per chunk)...[/blue]")

        if not tokens:
            console.print("[yellow]Warning: No tokens to caption[/yellow]")
            cmd = ["ffmpeg", "-y", "-i", str(video_path), "-c", "copy", str(output_path)]
            subprocess.run(cmd, capture_output=True, text=True)
            return output_path

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Get input video resolution to preserve it
        probe_cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video_path)
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        if probe_result.returncode == 0 and probe_result.stdout.strip():
            video_width, video_height = probe_result.stdout.strip().split(',')
            resolution = f"{video_width}x{video_height}"
            console.print(f"[dim]Preserving resolution: {resolution}[/dim]")
        else:
            resolution = None

        # Check for available filters
        has_drawtext = self._check_ffmpeg_filter("drawtext")
        has_ass = self._check_ffmpeg_filter(" ass ")  # Space-padded to avoid false matches

        if has_drawtext:
            # Use drawtext filter (best quality)
            console.print("[dim]Using drawtext filter for streaming captions[/dim]")
            filter_chain = self._build_drawtext_filter(tokens, max_words, caption_settings)

            encoder_args = get_encoder_args(self.encoder_config)
            cmd = [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-vf", filter_chain,
                *encoder_args,
                "-pix_fmt", "yuv420p",
            ]
            if resolution:
                cmd.extend(["-s", resolution])
            cmd.extend(["-c:a", "copy", str(output_path)])

        elif has_ass:
            # Use ASS subtitles with karaoke effect
            console.print("[dim]Using ASS subtitles with karaoke effect[/dim]")

            # Generate ASS file in output directory
            ass_path = output_path.parent / "streaming_captions.ass"
            self._generate_streaming_ass(tokens, ass_path, max_words)

            # Copy ASS to output dir for relative path
            original_cwd = os.getcwd()
            os.chdir(output_path.parent)

            try:
                encoder_args = get_encoder_args(self.encoder_config)
                cmd = [
                    "ffmpeg", "-y",
                    "-i", str(video_path),
                    "-vf", f"ass={ass_path.name}",
                    *encoder_args,
                    "-pix_fmt", "yuv420p",
                ]
                if resolution:
                    cmd.extend(["-s", resolution])
                cmd.extend(["-c:a", "copy", str(output_path)])

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                ) as progress:
                    progress.add_task("Encoding video with streaming captions...", total=None)
                    result = subprocess.run(cmd, capture_output=True, text=True)

                if result.returncode != 0:
                    raise RuntimeError(f"FFmpeg ASS caption burning failed: {result.stderr}")

                console.print(f"[green]✓[/green] Video with streaming captions saved to {output_path}")

                # Clean up ASS file
                if not self.config.keep_temp and ass_path.exists():
                    ass_path.unlink()

                return output_path
            finally:
                os.chdir(original_cwd)

        else:
            # No suitable filter available
            console.print("[yellow]Warning: FFmpeg not built with drawtext or ass filter.[/yellow]")
            console.print("[yellow]Install FFmpeg with libfreetype for streaming captions:[/yellow]")
            console.print("[dim]  brew install ffmpeg  (includes all filters)[/dim]")
            console.print("[yellow]Falling back to regular segment-based captions...[/yellow]")

            # Generate SRT from segments (convert tokens to segments)
            segments = self._tokens_to_segments(tokens, max_words)
            srt_path = output_path.parent / "captions.srt"
            self.generate_srt(segments, srt_path)

            # Use soft captions as fallback
            return self.add_soft_captions(video_path, srt_path, output_path)

        # Execute for drawtext path
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Encoding video with streaming captions...", total=None)
            result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg streaming caption burning failed: {result.stderr}")

        console.print(f"[green]✓[/green] Video with streaming captions saved to {output_path}")
        return output_path

    def _tokens_to_segments(self, tokens: list[Token], max_words: int = 20) -> list[Segment]:
        """Convert tokens to segments for fallback captioning."""
        chunks = self._chunk_tokens(tokens, max_words)
        segments = []

        for chunk in chunks:
            if not chunk:
                continue
            text = "".join(t.text for t in chunk).strip()
            text = self._ensure_punctuation_spacing(text)
            segments.append(Segment(
                start=chunk[0].start,
                end=chunk[-1].end,
                text=text
            ))

        return segments
