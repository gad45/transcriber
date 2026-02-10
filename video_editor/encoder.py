"""Video encoder configuration with hardware acceleration support."""

import subprocess
from dataclasses import dataclass


@dataclass
class EncoderConfig:
    """Configuration for video encoding."""
    use_hardware: bool = True
    quality: int = 70  # VideoToolbox quality (0-100, ~70 matches CRF 18)
    crf: int = 18  # libx264 fallback
    preset: str = "medium"


_videotoolbox_available: bool | None = None


def is_videotoolbox_available() -> bool:
    """Check if h264_videotoolbox encoder is available.

    Result is cached after first check.
    """
    global _videotoolbox_available

    if _videotoolbox_available is not None:
        return _videotoolbox_available

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True
        )
        _videotoolbox_available = "h264_videotoolbox" in result.stdout
    except Exception:
        _videotoolbox_available = False

    return _videotoolbox_available


def get_encoder_args(config: EncoderConfig | None = None) -> list[str]:
    """Get FFmpeg encoder arguments with automatic fallback.

    Args:
        config: Encoder configuration. Uses defaults if None.

    Returns:
        List of FFmpeg arguments for video encoding.
    """
    config = config or EncoderConfig()

    if config.use_hardware and is_videotoolbox_available():
        return [
            "-c:v", "h264_videotoolbox",
            "-q:v", str(config.quality),
            "-profile:v", "high",
            "-allow_sw", "true",
        ]
    else:
        return [
            "-c:v", "libx264",
            "-preset", config.preset,
            "-crf", str(config.crf),
        ]
