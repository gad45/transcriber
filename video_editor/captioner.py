"""Caption generation and burning module."""

from pathlib import Path
import subprocess
import re
import shutil
import tempfile

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .config import Config, CaptionStyle, CAPTION_STYLES
from .runtime_paths import ffmpeg_executable, ffprobe_executable
from .transcriber import Segment, Token
from .encoder import get_encoder_args, EncoderConfig

console = Console()
FFMPEG = ffmpeg_executable()
FFPROBE = ffprobe_executable()


class Captioner:
    """Handles SRT generation and caption burning."""

    def __init__(self, config: Config, encoder_config: EncoderConfig | None = None):
        self.config = config
        self.encoder_config = encoder_config or EncoderConfig(
            use_hardware=config.use_hardware_encoding
        )
    
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
            [FFMPEG, "-filters"],
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
                FFMPEG,
                "-y",
                "-hide_banner", "-loglevel", "error", "-nostats",
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
            FFMPEG,
            "-y",
            "-hide_banner", "-loglevel", "error", "-nostats",
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

    def _normalize_font_weight(self, font_weight: str | None) -> str:
        """Normalize GUI/project font weight values to export keys."""
        weight = str(font_weight or "bold").strip().lower()
        weight = re.sub(r"[\s_]+", "-", weight)
        weight = re.sub(r"-+", "-", weight)
        aliases = {
            "normal": "regular",
            "book": "regular",
            "roman": "regular",
            "semibold": "semi-bold",
            "demibold": "semi-bold",
            "demi-bold": "semi-bold",
            "extra-bold": "extra-bold",
            "extrabold": "extra-bold",
            "black": "extra-bold",
            "heavy": "extra-bold",
        }
        return aliases.get(weight, weight)

    def _font_style_for_weight(self, font_weight: str | None) -> str:
        """Map normalized caption weight to the closest font style name."""
        weight = self._normalize_font_weight(font_weight)
        return {
            "regular": "Regular",
            "medium": "Medium",
            "semi-bold": "Semibold",
            "bold": "Bold",
            "extra-bold": "Black",
        }.get(weight, "Bold")

    def _font_style_candidates(self, font_style: str) -> list[str]:
        """Return style aliases for fonts that name the same weight differently."""
        style_key = re.sub(r"[\s_-]+", "", str(font_style or "Regular").lower())
        if style_key in {"regular", "book", "roman", "normal"}:
            return ["Regular", "Book", "Roman"]
        if style_key == "medium":
            return ["Medium"]
        if style_key in {"semibold", "demibold", "demi"}:
            return ["Semibold", "SemiBold", "DemiBold", "Demi Bold"]
        if style_key == "bold":
            return ["Bold"]
        if style_key in {"black", "heavy", "extrabold"}:
            return ["Black", "Heavy", "ExtraBold", "Extra Bold"]
        return [font_style]

    def _normalize_font_name(self, value: str) -> str:
        """Normalize a font family/style/file name for coarse matching."""
        return re.sub(r"[^a-z0-9]+", "", str(value).lower())

    def _font_family_matches(self, requested_family: str, matched_families: str) -> bool:
        """Check that fontconfig did not fall back to an unrelated family."""
        requested = self._normalize_font_name(requested_family)
        if not requested:
            return False
        for family in matched_families.split(","):
            normalized = self._normalize_font_name(family)
            if normalized == requested or normalized.startswith(requested):
                return True
        return False

    def _font_style_matches(self, expected_style: str, matched_styles: str) -> bool:
        """Check that a resolved font file has the requested weight/style."""
        expected = {
            self._normalize_font_name(style)
            for style in self._font_style_candidates(expected_style)
        }
        for style in matched_styles.split(","):
            if self._normalize_font_name(style) in expected:
                return True
        return False

    def _fontconfig_command(self, command: str) -> str | None:
        """Find fontconfig even when the app is launched by Finder with a minimal PATH."""
        candidates = [
            shutil.which(command),
            f"/opt/homebrew/bin/{command}",
            f"/usr/local/bin/{command}",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def _resolve_font_file_with_fontconfig(self, fontname: str, font_style: str) -> str | None:
        """Resolve and verify a font file through fontconfig."""
        fc_match = self._fontconfig_command("fc-match")
        if not fc_match:
            return None

        fc_pattern = f"{fontname}:style={font_style}" if font_style != "Regular" else fontname
        try:
            result = subprocess.run(
                [fc_match, fc_pattern, "--format=%{file}\t%{family}\t%{style}"],
                capture_output=True, text=True, timeout=5
            )
        except subprocess.TimeoutExpired:
            return None

        if result.returncode != 0 or not result.stdout.strip():
            return None

        parts = result.stdout.strip().split("\t")
        if len(parts) < 3:
            return None

        file_path, matched_families, matched_styles = parts[:3]
        if not file_path:
            return None
        if not self._font_family_matches(fontname, matched_families):
            return None
        if not self._font_style_matches(font_style, matched_styles):
            return None
        return file_path

    def _resolve_font_file_from_paths(self, fontname: str, font_style: str) -> str | None:
        """Resolve a font file by scanning standard macOS font directories."""
        font_dirs = [
            Path.home() / "Library" / "Fonts",
            Path("/Library/Fonts"),
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
        ]
        family_key = self._normalize_font_name(fontname)
        style_keys = [
            self._normalize_font_name(style)
            for style in self._font_style_candidates(font_style)
        ]
        fallback_regular: Path | None = None

        for font_dir in font_dirs:
            if not font_dir.exists():
                continue
            for font_path in font_dir.rglob("*"):
                if font_path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                    continue
                stem_key = self._normalize_font_name(font_path.stem)
                if family_key not in stem_key:
                    continue
                if font_style == "Regular":
                    if "regular" in stem_key:
                        return str(font_path)
                    fallback_regular = fallback_regular or font_path
                    continue
                if any(style_key in stem_key for style_key in style_keys):
                    return str(font_path)

        if font_style == "Regular" and fallback_regular:
            return str(fallback_regular)
        return None

    def _resolve_font_file(self, fontname: str, font_style: str) -> str | None:
        """Resolve font family + style to a verified font file path."""
        fontname = str(fontname or "").strip()
        font_style = str(font_style or "Regular").strip()
        if not fontname:
            return None

        for style in self._font_style_candidates(font_style):
            font_file = self._resolve_font_file_with_fontconfig(fontname, style)
            if font_file:
                return font_file

        for style in self._font_style_candidates(font_style):
            font_file = self._resolve_font_file_from_paths(fontname, style)
            if font_file:
                return font_file

        return None

    def _quote_filter_value(self, value: str | Path) -> str:
        """Quote a value for an FFmpeg filter option."""
        escaped = str(value).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
        return f"'{escaped}'"

    def _escape_ass_override_value(self, value: str) -> str:
        """Remove characters that would break an ASS override tag."""
        return re.sub(r"[{}\\\r\n]", " ", str(value)).strip()

    def _escape_ass_text(self, value: str) -> str:
        """Escape text for ASS dialogue payloads."""
        text = str(value).replace("\\", r"\\")
        text = text.replace("{", r"\{").replace("}", r"\}")
        text = text.replace("\n", r"\N")
        return text

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
            font_weight = self._normalize_font_weight(caption_settings.get("font_weight", "bold"))
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

        font_style = self._font_style_for_weight(font_weight)

        # Resolve the actual font file path so drawtext does not pick Regular by
        # family name only. This matters when the app is launched outside a shell.
        font_file = self._resolve_font_file(fontname, font_style)
        if font_file:
            font_param = f"fontfile={self._quote_filter_value(font_file)}"
        else:
            font_param = f"font={self._quote_filter_value(fontname)}"

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
                    f":{font_param}"
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
                        f":{font_param}"
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
            [FFMPEG, "-filters"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return False
        return filter_name in result.stdout

    def _caption_events_for_preview(
        self,
        tokens: list[Token],
        max_words: int,
    ) -> list[tuple[float, float, str]]:
        """Return the caption states shown by the GUI preview.

        The preview presents a fixed-size box, accumulates words within each
        chunk, and keeps a completed chunk visible for 100 ms.  Keeping this
        timing logic here makes the export behave the same way instead of
        approximating the caption layout a second time.
        """
        events: list[tuple[float, float, str]] = []
        previous_end = 0.0

        for chunk in self._chunk_tokens(tokens, max_words):
            if not chunk:
                continue

            # The GUI checks chunks in order.  If the next chunk starts while
            # the prior one is still in its 100 ms grace period, it only becomes
            # visible after that prior chunk has cleared.
            chunk_start = max(chunk[0].start, previous_end)
            chunk_end = max(chunk[-1].end + 0.1, chunk_start + 0.01)

            state_times: list[float] = [chunk_start]
            for token in chunk:
                token_time = max(chunk_start, token.start)
                if token_time not in state_times:
                    state_times.append(token_time)
            state_times.sort()

            for index, start_time in enumerate(state_times):
                end_time = (
                    state_times[index + 1]
                    if index + 1 < len(state_times)
                    else chunk_end
                )
                if end_time <= start_time:
                    continue

                visible_tokens = [
                    token.text.strip()
                    for token in chunk
                    if token.start <= start_time
                ]
                if not visible_tokens:
                    # This can only happen when the first token was delayed by
                    # an overlapping prior chunk.  At that transition the GUI
                    # shows every word already reached by the playhead.
                    visible_tokens = [chunk[0].text.strip()]

                events.append((start_time, end_time, " ".join(visible_tokens)))

            previous_end = chunk_end

        return events

    @staticmethod
    def _qt_weight_for_caption(font_weight: str):
        """Map persisted caption weights to the weights used in the preview."""
        from PySide6.QtGui import QFont

        return {
            "regular": QFont.Weight.Normal,
            "medium": QFont.Weight.Medium,
            "semi-bold": QFont.Weight.DemiBold,
            "bold": QFont.Weight.Bold,
            "extra-bold": QFont.Weight.ExtraBold,
        }.get(font_weight, QFont.Weight.Bold)

    def _render_preview_caption_image(
        self,
        image_path: Path,
        text: str,
        caption_settings: dict | None,
        video_width: int,
        video_height: int,
    ) -> None:
        """Render one transparent caption frame using the GUI preview rules."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import (
            QColor,
            QFont,
            QGuiApplication,
            QImage,
            QPainter,
            QTextCharFormat,
            QTextCursor,
            QTextDocument,
            QTextOption,
        )

        # QFont needs an application instance when this is invoked from the
        # headless CLI.  The GUI already owns one, so retain only the CLI one.
        application = QGuiApplication.instance()
        if application is None:
            application = QGuiApplication(["video-editor-caption-renderer"])
            self._caption_render_application = application

        settings = caption_settings or {}
        font_size = int(settings.get("font_size", self.config.caption_font_size))
        font_family = str(settings.get("font_family", self.config.caption_font))
        font_weight = self._normalize_font_weight(settings.get("font_weight", "bold"))
        text_color = QColor(0, 0, 0) if settings.get("text_color") == "black" else QColor(255, 255, 255)
        show_background = bool(settings.get("show_background", True))
        pos_x = float(settings.get("pos_x", 0.5))
        pos_y = float(settings.get("pos_y", 0.92))
        box_width = float(settings.get("box_width", 0.6))
        box_height = float(settings.get("box_height", 0.07))

        box_w = max(1, min(video_width, round(box_width * video_width)))
        box_h = max(1, min(video_height, round(box_height * video_height)))
        box_x = round(pos_x * video_width - box_w / 2)
        box_y = round(pos_y * video_height - box_h)
        # This is identical to VideoPlayer.update_caption().
        box_x = max(0, min(box_x, video_width - box_w))
        box_y = max(0, min(box_y, video_height - box_h))

        image = QImage(video_width, video_height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            if show_background:
                # The preview uses this fixed semi-transparent black brush for
                # either text colour, so exports deliberately do the same.
                painter.fillRect(box_x, box_y, box_w, box_h, QColor(0, 0, 0, 180))

            font = QFont(font_family, font_size)
            font.setWeight(self._qt_weight_for_caption(font_weight))

            document = QTextDocument()
            document.setDefaultFont(font)
            option = document.defaultTextOption()
            option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            document.setDefaultTextOption(option)
            document.setTextWidth(max(1, box_w - 20))
            document.setPlainText(text)

            cursor = QTextCursor(document)
            cursor.select(QTextCursor.SelectionType.Document)
            char_format = QTextCharFormat()
            char_format.setForeground(text_color)
            cursor.mergeCharFormat(char_format)

            painter.translate(box_x + 10, box_y + 5)
            document.drawContents(painter)
        finally:
            painter.end()

        if not image.save(str(image_path), "PNG"):
            raise RuntimeError(f"Could not render caption image: {image_path}")

    def _burn_streaming_captions_preview_renderer(
        self,
        video_path: Path,
        tokens: list[Token],
        output_path: Path,
        max_words: int,
        caption_settings: dict | None,
        video_width: int,
        video_height: int,
    ) -> Path:
        """Burn captions with Qt when FFmpeg has no text-rendering filter.

        The packaged FFmpeg deliberately stays small and can omit libass and
        libfreetype.  Unlike the former soft-subtitle fallback, this produces a
        real video overlay and follows the GUI preview's text and box geometry.
        """
        console.print("[dim]Using the preview renderer for burned-in captions[/dim]")
        events = self._caption_events_for_preview(tokens, max_words)
        if not events:
            raise RuntimeError("No caption frames could be generated for this export.")

        with tempfile.TemporaryDirectory(
            prefix="video_editor_captions_", dir=output_path.parent
        ) as temp_directory:
            render_dir = Path(temp_directory)
            clear_image = render_dir / "clear.png"
            self._render_preview_caption_image(
                clear_image, "", {"show_background": False}, video_width, video_height
            )

            manifest_lines = ["ffconcat version 1.0"]

            def append_frame(filename: str, duration: float) -> None:
                if duration <= 0:
                    return
                manifest_lines.append(f"file '{filename}'")
                manifest_lines.append(f"duration {duration:.6f}")

            cursor = 0.0
            for index, (start_time, end_time, text) in enumerate(events):
                if start_time > cursor:
                    append_frame(clear_image.name, start_time - cursor)

                frame_image = render_dir / f"caption_{index:05d}.png"
                self._render_preview_caption_image(
                    frame_image,
                    text,
                    caption_settings,
                    video_width,
                    video_height,
                )
                append_frame(frame_image.name, end_time - max(cursor, start_time))
                cursor = max(cursor, end_time)

            # A final transparent frame makes the end-of-caption state explicit
            # before overlay reaches EOF and passes through the source video.
            append_frame(clear_image.name, 0.1)
            manifest_path = render_dir / "captions.ffconcat"
            manifest_path.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

            encoder_args = get_encoder_args(self.encoder_config)
            filter_complex = (
                "[1:v]format=rgba,setpts=PTS-STARTPTS[captions];"
                "[0:v][captions]overlay=0:0:eof_action=pass:repeatlast=0:format=auto[video]"
            )
            cmd = [
                FFMPEG, "-y",
                "-hide_banner", "-loglevel", "error", "-nostats",
                "-i", str(video_path),
                "-f", "concat", "-safe", "0", "-i", manifest_path.name,
                "-filter_complex", filter_complex,
                "-map", "[video]",
                "-map", "0:a?",
                *encoder_args,
                "-pix_fmt", "yuv420p",
                "-c:a", "copy",
                str(output_path),
            ]

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                progress.add_task("Encoding video with preview-matched captions...", total=None)
                result = subprocess.run(
                    cmd,
                    cwd=render_dir,
                    capture_output=True,
                    text=True,
                )

            if result.returncode != 0:
                raise RuntimeError(
                    "FFmpeg preview caption burning failed: "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )

        console.print(f"[green]✓[/green] Video with burned-in captions saved to {output_path}")
        return output_path

    def _generate_streaming_ass(
        self,
        tokens: list[Token],
        output_path: Path,
        max_words: int = 15,
        caption_settings: dict | None = None,
        video_width: int = 1920,
        video_height: int = 1080,
    ) -> Path:
        """
        Generate an ASS subtitle file with streaming word-by-word captions.

        Emits one event per revealed word so future words are not visible before
        their start time. This preserves the preview/drawtext streaming behavior
        while keeping the file-based ASS export path.

        Args:
            tokens: List of word-level tokens
            output_path: Path for the ASS file
            max_words: Maximum words per display chunk

        Returns:
            Path to the generated ASS file
        """
        chunks = self._chunk_tokens(tokens, max_words)

        # Style settings from GUI (fallback to defaults when absent).
        if caption_settings:
            font_size = int(caption_settings.get("font_size", self.config.caption_font_size))
            font_family = str(caption_settings.get("font_family", "Arial")).replace(",", " ")
            font_weight = self._normalize_font_weight(caption_settings.get("font_weight", "bold"))
            text_color = caption_settings.get("text_color", "white")
            show_background = bool(caption_settings.get("show_background", True))
            pos_x = float(caption_settings.get("pos_x", 0.5))
            pos_y = float(caption_settings.get("pos_y", 0.92))
            box_width = float(caption_settings.get("box_width", 0.6))
        else:
            font_size = self.config.caption_font_size
            font_family = "Arial"
            font_weight = "bold"
            text_color = "white"
            show_background = True
            pos_x = 0.5
            pos_y = 0.92
            box_width = 0.6

        # ASS color format: &HAABBGGRR (AA: alpha, 00=opaque, FF=transparent)
        primary_color = "&H00000000" if text_color == "black" else "&H00FFFFFF"
        outline_color = "&H00FFFFFF" if text_color == "black" else "&H00000000"
        if show_background:
            # Approximate black/white 70% opaque background.
            back_color = "&H4DFFFFFF" if text_color == "black" else "&H4D000000"
            border_style = 3  # Opaque box
            outline = 0
            shadow = 0
        else:
            back_color = "&HFF000000"  # Fully transparent
            border_style = 1
            outline = 3
            shadow = 1

        # ASS style Bold uses -1 for true. Inline \b1 below reinforces the
        # style because libass/font providers can otherwise fall back to Regular.
        is_bold = font_weight in {"semi-bold", "bold", "extra-bold"}
        bold = -1 if is_bold else 0
        bold_override = 1 if is_bold else 0
        font_override = (
            f"{{\\fn{self._escape_ass_override_value(font_family)}\\b{bold_override}}}"
        )

        # Use box width to approximate line wrapping similar to UI caption box.
        box_px = max(120.0, box_width * max(1, video_width))
        # Approx average Latin glyph width factor.
        chars_per_line = max(14, int(box_px / max(10.0, font_size * 0.55)))
        approx_words_per_line = max(4, min(max_words, chars_per_line // 6))

        # Convert normalized UI position to ASS absolute position.
        pos_x_px = max(0, min(video_width, int(pos_x * video_width)))
        pos_y_px = max(0, min(video_height, int(pos_y * video_height)))

        # ASS header with style definition
        ass_content = """[Script Info]
Title: Streaming Captions
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_family},{font_size},{primary_color},&H000000FF,{outline_color},{back_color},{bold},0,0,0,100,100,0,0,{border_style},{outline},{shadow},2,20,20,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""".format(
            play_res_x=video_width,
            play_res_y=video_height,
            font_family=font_family,
            font_size=font_size,
            primary_color=primary_color,
            outline_color=outline_color,
            back_color=back_color,
            bold=bold,
            border_style=border_style,
            outline=outline,
            shadow=shadow,
        )

        def format_ass_time(seconds: float) -> str:
            """Convert seconds to ASS timestamp format (H:MM:SS.CC)."""
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            centis = int((seconds % 1) * 100)
            return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"

        # Generate dialogue lines for each revealed word.
        for chunk in chunks:
            if not chunk:
                continue

            chunk_end = chunk[-1].end + 0.1

            for i, token in enumerate(chunk):
                accumulated_text = "".join(t.text for t in chunk[:i + 1]).strip()
                accumulated_text = self._ensure_punctuation_spacing(accumulated_text)
                lines = self._split_into_lines(
                    accumulated_text,
                    words_per_line=approx_words_per_line,
                )
                visible_text = r"\N".join(self._escape_ass_text(line) for line in lines)

                delay = self.config.caption_delay
                word_start = token.start + delay
                if i < len(chunk) - 1:
                    word_end = chunk[i + 1].start + delay
                else:
                    word_end = chunk_end + delay

                if word_end <= word_start:
                    word_end = word_start + 0.01

                start_time = format_ass_time(word_start)
                end_time = format_ass_time(word_end)
                ass_content += (
                    f"Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,"
                    f"{{\\pos({pos_x_px},{pos_y_px})}}{font_override}{visible_text}\n"
                )

        output_path = Path(output_path)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(ass_content)

        return output_path

    def _burn_streaming_captions_ass(
        self,
        video_path: Path,
        tokens: list[Token],
        output_path: Path,
        max_words: int,
        resolution: str | None,
        caption_settings: dict | None,
        video_width: int,
        video_height: int,
    ) -> Path:
        """Burn streaming captions using ASS subtitles."""
        import os

        console.print("[dim]Using ASS subtitles for streaming captions[/dim]")

        ass_path = output_path.parent / "streaming_captions.ass"
        self._generate_streaming_ass(
            tokens,
            ass_path,
            max_words=max_words,
            caption_settings=caption_settings,
            video_width=video_width,
            video_height=video_height,
        )
        if caption_settings:
            font_family = str(caption_settings.get("font_family", "Arial")).replace(",", " ")
            font_weight = self._normalize_font_weight(caption_settings.get("font_weight", "bold"))
        else:
            font_family = "Arial"
            font_weight = "bold"
        font_file = self._resolve_font_file(
            font_family,
            self._font_style_for_weight(font_weight),
        )

        ass_filter = f"ass={self._quote_filter_value(ass_path.name)}"
        if font_file:
            ass_filter += f":fontsdir={self._quote_filter_value(Path(font_file).parent)}"

        original_cwd = os.getcwd()
        os.chdir(output_path.parent)

        try:
            encoder_args = get_encoder_args(self.encoder_config)
            cmd = [
                FFMPEG, "-y",
                "-hide_banner", "-loglevel", "error", "-nostats",
                "-i", str(video_path),
                "-vf", ass_filter,
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

            if not self.config.keep_temp and ass_path.exists():
                ass_path.unlink()

            return output_path
        finally:
            os.chdir(original_cwd)

    def burn_streaming_captions(
        self,
        video_path: Path,
        tokens: list[Token],
        output_path: Path,
        max_words: int = 15,
        caption_settings: dict = None
    ) -> Path:
        """
        Burn streaming captions into video using FFmpeg.

        Exports that include GUI caption settings use the Qt preview renderer
        so their font, weight, fixed box, and placement exactly match Preview.
        Legacy callers without GUI settings retain the ASS/drawtext paths.

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
        video_path = Path(video_path).resolve()
        output_path = Path(output_path).resolve()

        console.print(f"[blue]Burning streaming captions ({len(tokens)} words, max {max_words} per chunk)...[/blue]")

        if not tokens:
            console.print("[yellow]Warning: No tokens to caption[/yellow]")
            cmd = [
                FFMPEG, "-y",
                "-hide_banner", "-loglevel", "error", "-nostats",
                "-i", str(video_path),
                "-c", "copy",
                str(output_path)
            ]
            subprocess.run(cmd, capture_output=True, text=True)
            return output_path

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Get input video resolution to preserve it
        probe_cmd = [
            FFPROBE, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video_path)
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        if probe_result.returncode == 0 and probe_result.stdout.strip():
            video_width, video_height = probe_result.stdout.strip().split(',')
            video_width = int(video_width)
            video_height = int(video_height)
            resolution = f"{video_width}x{video_height}"
            console.print(f"[dim]Preserving resolution: {resolution}[/dim]")
        else:
            video_width, video_height = 1920, 1080
            resolution = None

        # The GUI's fixed caption box is a Qt layout.  ASS and drawtext can
        # approximate it, but cannot reproduce its geometry and wrapping
        # exactly.  Use the same Qt rendering primitives whenever the caller
        # supplied the preview settings, regardless of installed FFmpeg filters.
        if caption_settings is not None:
            return self._burn_streaming_captions_preview_renderer(
                video_path,
                tokens,
                output_path,
                max_words,
                caption_settings,
                video_width,
                video_height,
            )

        # Prefer the file-based ASS path for legacy/headless callers that have
        # no GUI appearance to match. Long videos can produce thousands of
        # drawtext filters, which makes the FFmpeg command fragile and slow.
        has_ass = self._check_ffmpeg_filter(" ass ")  # Space-padded to avoid false matches
        has_drawtext = self._check_ffmpeg_filter("drawtext")

        if has_ass:
            return self._burn_streaming_captions_ass(
                video_path,
                tokens,
                output_path,
                max_words,
                resolution,
                caption_settings,
                video_width,
                video_height,
            )

        if has_drawtext:
            # Use drawtext when libass is unavailable.
            console.print("[dim]Using drawtext filter for streaming captions[/dim]")
            filter_chain = self._build_drawtext_filter(tokens, max_words, caption_settings)

            encoder_args = get_encoder_args(self.encoder_config)
            cmd = [
                FFMPEG, "-y",
                "-hide_banner", "-loglevel", "error", "-nostats",
                "-i", str(video_path),
                "-vf", filter_chain,
                *encoder_args,
                "-pix_fmt", "yuv420p",
            ]
            if resolution:
                cmd.extend(["-s", resolution])
            cmd.extend(["-c:a", "copy", str(output_path)])

        else:
            # Do not silently replace a burn-in export with selectable soft
            # subtitles.  This happens with the bundled FFmpeg build, which
            # intentionally has neither libass nor libfreetype.  Rendering the
            # overlay through Qt preserves the preview's font, weight, box, and
            # normalized placement before FFmpeg encodes the final video.
            return self._burn_streaming_captions_preview_renderer(
                video_path,
                tokens,
                output_path,
                max_words,
                caption_settings,
                video_width,
                video_height,
            )

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
