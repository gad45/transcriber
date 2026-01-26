"""Transcription module using Soniox API for Hungarian speech-to-text."""

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from .config import Config

console = Console()

SONIOX_API_BASE_URL = "https://api.soniox.com"


@dataclass
class Token:
    """A single word/token with timing information."""
    text: str
    start: float  # Start time in seconds
    end: float    # End time in seconds

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Segment:
    """A transcribed segment of speech."""
    start: float  # Start time in seconds
    end: float    # End time in seconds
    text: str     # Transcribed text
    confidence: float = 1.0  # Confidence score (0-1)
    tokens: list[Token] | None = None  # Word-level tokens (optional)

    @property
    def duration(self) -> float:
        """Duration of the segment in seconds."""
        return self.end - self.start

    def to_srt_time(self, seconds: float) -> str:
        """Convert seconds to SRT timestamp format."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def to_srt_entry(self, index: int) -> str:
        """Convert segment to SRT entry format."""
        return f"{index}\n{self.to_srt_time(self.start)} --> {self.to_srt_time(self.end)}\n{self.text}\n"


class Transcriber:
    """Handles audio extraction and transcription using Soniox API."""

    def __init__(self, config: Config):
        self.config = config
        self._session = None
        self._api_key = os.getenv("SONIOX_API_KEY")

        if not self._api_key:
            raise ValueError("SONIOX_API_KEY environment variable not set")

        console.print("[green]✓[/green] Using Soniox API for transcription")

    @property
    def session(self) -> requests.Session:
        """Get authenticated session for Soniox API."""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers["Authorization"] = f"Bearer {self._api_key}"
        return self._session

    def extract_audio(self, video_path: Path, output_path: Path | None = None) -> Path:
        """
        Extract audio from video using FFmpeg.

        Args:
            video_path: Path to the input video
            output_path: Optional output path for audio (default: temp file)

        Returns:
            Path to the extracted audio file
        """
        if output_path is None:
            temp_dir = self.config.temp_dir or Path(tempfile.gettempdir())
            output_path = temp_dir / f"{video_path.stem}_audio.mp3"

        console.print(f"[blue]Extracting audio from {video_path.name}...[/blue]")

        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-vn",  # No video
            "-acodec", "libmp3lame",  # MP3 for smaller file size
            "-ar", "16000",  # 16kHz sample rate
            "-ac", "1",  # Mono
            "-b:a", "64k",  # 64kbps bitrate
            "-y",  # Overwrite output
            str(output_path)
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr}")

        console.print(f"[green]✓[/green] Audio extracted to {output_path.name}")
        return output_path

    def _upload_file(self, audio_path: Path) -> str:
        """Upload audio file to Soniox."""
        console.print(f"[blue]Uploading audio to Soniox...[/blue]")

        with open(audio_path, "rb") as f:
            res = self.session.post(
                f"{SONIOX_API_BASE_URL}/v1/files",
                files={"file": (audio_path.name, f, "audio/mpeg")},
            )

        if res.status_code not in (200, 201):
            raise RuntimeError(f"Soniox file upload failed: {res.status_code} - {res.text}")

        file_id = res.json()["id"]
        console.print(f"[green]✓[/green] Audio uploaded (file_id: {file_id[:8]}...)")
        return file_id

    def _create_transcription(self, file_id: str) -> str:
        """Create a transcription job for the uploaded file."""
        config = {
            "model": "stt-async-v3",
            "file_id": file_id,
            "language_hints": ["hu"],  # Hungarian
            "enable_speaker_diarization": False,
        }

        res = self.session.post(
            f"{SONIOX_API_BASE_URL}/v1/transcriptions",
            json=config,
        )

        if res.status_code not in (200, 201):
            raise RuntimeError(f"Soniox transcription creation failed: {res.status_code} - {res.text}")

        transcription_id = res.json()["id"]
        console.print(f"[blue]Transcription job created (id: {transcription_id[:8]}...)[/blue]")
        return transcription_id

    def _wait_for_completion(self, transcription_id: str) -> None:
        """Poll for transcription completion."""
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Transcribing with Soniox...", total=None)

            while True:
                res = self.session.get(
                    f"{SONIOX_API_BASE_URL}/v1/transcriptions/{transcription_id}"
                )

                if res.status_code != 200:
                    raise RuntimeError(f"Soniox status check failed: {res.status_code} - {res.text}")

                data = res.json()
                status = data["status"]

                if status == "completed":
                    progress.update(task, description="[green]Transcription complete![/green]")
                    return
                elif status == "error":
                    raise RuntimeError(f"Soniox transcription failed: {data.get('error_message', 'Unknown error')}")

                # Still processing
                time.sleep(2)

    def _get_transcript(self, transcription_id: str) -> tuple[list[Segment], list[Token]]:
        """Retrieve the transcript and convert to segments and tokens."""
        res = self.session.get(
            f"{SONIOX_API_BASE_URL}/v1/transcriptions/{transcription_id}/transcript"
        )

        if res.status_code != 200:
            raise RuntimeError(f"Soniox transcript retrieval failed: {res.status_code} - {res.text}")

        data = res.json()
        raw_tokens = data.get("tokens", [])

        if not raw_tokens:
            return [], []

        # Convert to Token objects and collect all tokens
        all_tokens: list[Token] = []
        for tok in raw_tokens:
            text = tok.get("text", "")
            if not text.strip():
                continue
            all_tokens.append(Token(
                text=text,
                start=tok.get("start_ms", 0) / 1000.0,
                end=tok.get("end_ms", 0) / 1000.0
            ))

        # Group tokens into segments (by sentence/pause)
        segments = []
        current_segment_tokens: list[Token] = []
        current_start = None
        current_end = None

        for i, token in enumerate(all_tokens):
            # Start new segment
            if current_start is None:
                current_start = token.start

            current_segment_tokens.append(token)
            current_end = token.end

            # Check if this is end of sentence or long pause
            is_sentence_end = token.text.rstrip().endswith(('.', '!', '?', ','))
            gap_to_next = 0

            # Check gap to next token
            if i < len(all_tokens) - 1:
                gap_to_next = all_tokens[i + 1].start - token.end

            # Create segment if sentence ends or there's a significant pause
            if is_sentence_end or gap_to_next > 0.5 or i == len(all_tokens) - 1:
                if current_segment_tokens and current_start is not None:
                    segment_text = "".join(t.text for t in current_segment_tokens).strip()
                    if segment_text and (current_end - current_start) >= self.config.min_segment_duration:
                        segments.append(Segment(
                            start=current_start,
                            end=current_end,
                            text=segment_text,
                            confidence=1.0,
                            tokens=current_segment_tokens.copy()
                        ))

                # Reset for next segment
                current_segment_tokens = []
                current_start = None
                current_end = None

        # Merge sub-word tokens into full words for caption display
        word_tokens = self._merge_tokens_to_words(all_tokens)

        return segments, word_tokens

    def _merge_tokens_to_words(self, tokens: list[Token]) -> list[Token]:
        """
        Merge sub-word tokens into full words.

        Soniox returns tokens at syllable level (e.g., 'E', 'b', 'ben' for 'Ebben').
        This merges them into complete words for proper caption display.

        A new word starts when:
        - Token text starts with a space
        - There's a significant time gap (>0.3s) between tokens
        """
        if not tokens:
            return []

        word_tokens: list[Token] = []
        current_word_parts: list[Token] = []

        for i, token in enumerate(tokens):
            # Check if this token starts a new word
            starts_new_word = False

            if token.text.startswith(' '):
                starts_new_word = True
            elif current_word_parts:
                # Check for time gap
                gap = token.start - current_word_parts[-1].end
                if gap > 0.3:
                    starts_new_word = True

            # If starting new word, flush the current word
            if starts_new_word and current_word_parts:
                word_text = "".join(t.text for t in current_word_parts)
                word_tokens.append(Token(
                    text=word_text,
                    start=current_word_parts[0].start,
                    end=current_word_parts[-1].end
                ))
                current_word_parts = []

            current_word_parts.append(token)

        # Flush the last word
        if current_word_parts:
            word_text = "".join(t.text for t in current_word_parts)
            word_tokens.append(Token(
                text=word_text,
                start=current_word_parts[0].start,
                end=current_word_parts[-1].end
            ))

        return word_tokens

    def _cleanup(self, file_id: str | None, transcription_id: str | None) -> None:
        """Clean up uploaded file and transcription from Soniox."""
        if transcription_id:
            try:
                self.session.delete(f"{SONIOX_API_BASE_URL}/v1/transcriptions/{transcription_id}")
            except Exception:
                pass

        if file_id:
            try:
                self.session.delete(f"{SONIOX_API_BASE_URL}/v1/files/{file_id}")
            except Exception:
                pass

    def transcribe(self, audio_path: Path) -> tuple[list[Segment], list[Token]]:
        """
        Transcribe audio file using Soniox API.

        Args:
            audio_path: Path to the audio file

        Returns:
            Tuple of (segments, tokens) - segments for analysis, tokens for streaming captions
        """
        console.print(f"[blue]Transcribing audio (language: Hungarian)...[/blue]")

        file_id = None
        transcription_id = None

        try:
            # Upload file
            file_id = self._upload_file(audio_path)

            # Create transcription job
            transcription_id = self._create_transcription(file_id)

            # Wait for completion
            self._wait_for_completion(transcription_id)

            # Get transcript
            segments, tokens = self._get_transcript(transcription_id)

            console.print(f"[green]✓[/green] Transcribed {len(segments)} segments ({len(tokens)} words)")
            return segments, tokens

        finally:
            # Clean up remote resources
            if not self.config.keep_temp:
                self._cleanup(file_id, transcription_id)

    def transcribe_video(self, video_path: Path) -> tuple[list[Segment], list[Token]]:
        """
        Full pipeline: extract audio and transcribe.

        Args:
            video_path: Path to the input video

        Returns:
            Tuple of (segments, tokens)
        """
        video_path = Path(video_path)

        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        # Extract audio
        audio_path = self.extract_audio(video_path)

        try:
            # Transcribe
            segments, tokens = self.transcribe(audio_path)
            return segments, tokens
        finally:
            # Clean up temp audio if not keeping
            if not self.config.keep_temp and audio_path.exists():
                audio_path.unlink()
