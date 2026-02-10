"""Background FFmpeg tasks for recorder post-processing."""

from __future__ import annotations

import subprocess
from pathlib import Path


class FFmpegCropWorker:
    """Run an FFmpeg crop job in a background thread."""

    def __init__(
        self,
        input_path: Path,
        output_path: Path,
        crop_filter: str,
        encoder_args: list[str],
    ) -> None:
        self._input_path = input_path
        self._output_path = output_path
        self._crop_filter = crop_filter
        self._encoder_args = encoder_args
        self._process: subprocess.Popen | None = None
        self._cancelled = False

    def run(self) -> tuple[bool, Path, str]:
        """Execute the crop task and return `(success, output_path, message)`."""
        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(self._input_path),
                "-vf",
                self._crop_filter,
                *self._encoder_args,
                "-c:a",
                "copy",
                str(self._output_path),
            ]

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            _, stderr = self._process.communicate()

            if self._cancelled:
                message = (
                    "Cropping cancelled. Raw recording saved at:\n"
                    f"{self._input_path}"
                )
                return False, self._input_path, message

            if self._process.returncode == 0 and self._output_path.exists():
                return True, self._output_path, ""

            message = (
                "Cropping failed. Raw recording saved at:\n"
                f"{self._input_path}"
            )
            if stderr:
                lines = stderr.strip().splitlines()
                if lines:
                    message += f"\n\nFFmpeg error: {lines[-1]}"
            return False, self._input_path, message

        except Exception as exc:
            return False, self._input_path, str(exc)

    def cancel(self) -> None:
        """Request cancellation of the FFmpeg process."""
        self._cancelled = True
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
