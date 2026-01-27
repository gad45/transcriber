"""Configuration settings for the video editor."""

from pydantic import BaseModel, Field
from enum import Enum
from pathlib import Path


class CaptionStyle(str, Enum):
    """Available caption styles."""
    MINIMAL = "minimal"
    MODERN = "modern"
    BOLD = "bold"


class Config(BaseModel):
    """Configuration for the video editor."""
    
    # Analysis settings
    silence_threshold: float = Field(
        default=1.5,
        ge=0.5,
        le=10.0,
        description="Minimum silence duration (seconds) to cut"
    )
    retake_similarity: float = Field(
        default=0.8,
        ge=0.5,
        le=1.0,
        description="Similarity threshold for retake detection (0-1)"
    )
    min_segment_duration: float = Field(
        default=0.5,
        description="Minimum duration for a speech segment (seconds)"
    )
    
    # Hungarian filler words to detect
    filler_words: list[str] = Field(
        default=["öö", "hát", "izé", "szóval", "tehát", "na", "nos", "ööö", "hmm"],
        description="Filler words to detect in Hungarian"
    )
    
    # Caption settings
    caption_style: CaptionStyle = Field(
        default=CaptionStyle.MODERN,
        description="Style for burned-in captions"
    )
    caption_font_size: int = Field(
        default=24,
        ge=12,
        le=72,
        description="Font size for captions"
    )
    caption_font: str = Field(
        default="Arial",
        description="Font for captions"
    )
    streaming_captions: bool = Field(
        default=False,
        description="Enable word-by-word streaming captions (burned in)"
    )
    max_caption_words: int = Field(
        default=15,
        ge=5,
        le=50,
        description="Maximum words on screen at once for streaming captions"
    )
    
    # Processing settings
    temp_dir: Path | None = Field(
        default=None,
        description="Temporary directory for processing (None = system temp)"
    )
    keep_temp: bool = Field(
        default=False,
        description="Keep temporary files after processing"
    )
    
    # LLM settings for take selection
    openai_api_key: str | None = Field(
        default=None,
        description="OpenAI API key for LLM-based take selection"
    )
    llm_model: str = Field(
        default="gpt-4o-mini",
        description="LLM model for take selection"
    )

    # QC settings
    qc_enabled: bool = Field(
        default=True,
        description="Enable transcription quality control"
    )
    qc_auto_correct: bool = Field(
        default=True,
        description="Automatically apply QC corrections"
    )
    qc_model: str = Field(
        default="gemini-2.0-flash",
        description="Gemini model for QC (update when gemini-3-flash available)"
    )


# Caption style presets
CAPTION_STYLES = {
    CaptionStyle.MINIMAL: {
        "FontSize": 20,
        "FontName": "Arial",
        "PrimaryColour": "&HFFFFFF",
        "OutlineColour": "&H000000",
        "Outline": 1,
        "Shadow": 0,
    },
    CaptionStyle.MODERN: {
        "FontSize": 24,
        "FontName": "Arial",
        "PrimaryColour": "&HFFFFFF",
        "OutlineColour": "&H000000",
        "BackColour": "&H80000000",
        "Outline": 2,
        "Shadow": 1,
        "BorderStyle": 3,
    },
    CaptionStyle.BOLD: {
        "FontSize": 28,
        "FontName": "Arial",
        "Bold": 1,
        "PrimaryColour": "&H00FFFF",
        "OutlineColour": "&H000000",
        "Outline": 3,
        "Shadow": 2,
    },
}
