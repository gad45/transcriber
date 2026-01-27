# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-powered video editing tool for Hungarian spoken content. Automatically removes bad takes, silences, and adds captions using Whisper AI for transcription and LLMs (Gemini/OpenAI) for take selection.

## Common Commands

```bash
# Install dependencies
source venv/bin/activate
pip install -e .

# Run the CLI video editor
python -m video_editor input.mp4 -o output.mp4

# Preview mode (show proposed cuts without processing)
python -m video_editor input.mp4 --preview

# Run with specific options
python -m video_editor input.mp4 --model medium --silence-threshold 1.5 --caption-style modern

# Launch the GUI
python -m video_editor.gui_main
python -m video_editor.gui_main path/to/video.mp4  # With a video file

# Run tests
pytest
```

## Environment Variables

Create a `.env` file in the project root:

```bash
GEMINI_API_KEY=your_key_here     # Primary LLM for take selection (Gemini 2.0 Flash)
OPENAI_API_KEY=your_key_here     # Fallback LLM for take selection
WHISPER_FORCE_CPU=1              # Force CPU for transcription (MPS/Apple Silicon has known issues)
```

## Architecture

### Core Pipeline (CLI)

The processing pipeline flows through four main components:

1. **Transcriber** (`video_editor/transcriber.py`) - Extracts audio via FFmpeg, runs Whisper for Hungarian speech-to-text. Returns timestamped `Segment` and `Token` objects.

2. **Analyzer** (`video_editor/analyzer.py`) - Detects silences, identifies retakes using fuzzy matching (rapidfuzz), and selects best takes via LLM. Returns `TimeRange` objects marking segments to keep.

3. **Cutter** (`video_editor/cutter.py`) - Extracts segments using FFmpeg stream copy (no re-encoding for speed), then concatenates them.

4. **Captioner** (`video_editor/captioner.py`) - Generates SRT files and either burns captions into video (libx264) or adds soft subtitles.

### GUI Application

The GUI is built with PySide6 (Qt6) and provides:

- **MainWindow** (`video_editor/gui/main_window.py`) - Main application orchestrating all components
- **VideoPlayer** (`video_editor/gui/video_player.py`) - QMediaPlayer-based video playback
- **Timeline** (`video_editor/gui/timeline.py`) - QGraphicsView-based segment visualization
- **TranscriptEditor** (`video_editor/gui/transcript_editor.py`) - Text editing for transcription
- **SegmentItem** (`video_editor/gui/segment_item.py`) - Graphics items for timeline (segments, highlights, playhead)
- **Models** (`video_editor/gui/models.py`) - EditSession state management

### Key Data Structures

**Core:**
- `Segment` - Transcribed speech segment with start/end times, text, confidence
- `Token` - Word-level timing for streaming captions
- `TimeRange` - Start/end times for video cutting
- `RetakeGroup` - Multiple segments that are retakes of each other
- `AnalyzedSegment` - Segment with action (KEEP/REMOVE) and reason

**GUI:**
- `EditSession` - Complete editing state (segments, tokens, user edits, highlights)
- `HighlightRegion` - User-defined force-include region for non-speech content

### LLM Integration

Take selection prefers Gemini 2.0 Flash (via `google-genai` package), falls back to OpenAI, then to duration-based selection if no API keys are available.

## File Structure

```
video_editor/
├── __init__.py
├── main.py              # CLI entry point
├── config.py            # Configuration management
├── transcriber.py       # Whisper transcription
├── analyzer.py          # Retake detection & take selection
├── cutter.py            # FFmpeg video cutting
├── captioner.py         # Caption generation & burning
├── qc.py                # Quality control checks
├── gui_main.py          # GUI entry point
└── gui/
    ├── __init__.py
    ├── main_window.py   # Main application window
    ├── video_player.py  # Video playback widget
    ├── timeline.py      # Timeline visualization
    ├── segment_item.py  # Graphics items (SegmentItem, HighlightItem, PlayheadItem)
    ├── transcript_editor.py  # Text editing panel
    └── models.py        # Data models (EditSession, HighlightRegion)
```

## GUI Signal Flow

```
User clicks timeline → TimelineView.mousePressEvent
                     → seek_requested signal
                     → MainWindow._on_seek_requested
                     → VideoPlayer.seek_seconds

User creates highlight → TimelineView mouse drag
                       → highlight_created signal
                       → MainWindow._on_highlight_created
                       → EditSession.add_highlight
                       → Timeline.add_highlight

User toggles segment → TranscriptEditor checkbox
                     → keep_changed signal
                     → MainWindow._on_segment_keep_changed
                     → EditSession.set_segment_kept
                     → Timeline.update_segment
```

## FFmpeg Notes

- Audio extraction uses 16kHz mono WAV (optimal for Whisper)
- Video cutting uses stream copy (`-c copy`) for speed
- Caption burning requires re-encoding with libx264
- Segment gap is 0.05s between cuts to avoid audio glitches

## Common Tasks

### Adding a new timeline feature
1. Add data model to `gui/models.py` (with save/load support)
2. Add graphics item to `gui/segment_item.py`
3. Add signals and handlers to `gui/timeline.py`
4. Connect signals in `gui/main_window.py`

### Modifying export behavior
1. Check `gui/main_window.py:_export_video()`
2. `EditSession.get_final_keep_ranges()` determines what's included
3. `EditSession.get_final_tokens()` provides caption timing
4. Tokens are adjusted for cuts via `_adjust_tokens_for_cuts()`

### Adding keyboard shortcuts
1. Add to `gui/main_window.py:_setup_shortcuts()`
2. Use `QShortcut(QKeySequence(...), self)`
3. Connect to appropriate handler

## Testing the GUI

```bash
# Launch with test video
python -m video_editor.gui_main test/test.mp4

# Test highlight feature
# 1. Analyze video
# 2. Click and drag on timeline empty space (> 0.5s)
# 3. Blue highlight should appear
# 4. Right-click to remove
# 5. Save project and reload to verify persistence
```

## Troubleshooting

- **API key errors**: Ensure `.env` file exists and `load_dotenv()` is called
- **Slow Whisper on Mac**: Set `WHISPER_FORCE_CPU=1`
- **GUI import errors**: Install PySide6: `pip install PySide6`
- **Export no captions**: Check tokens exist and segments are kept
