# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-powered video editing tool for Hungarian spoken content. Automatically removes bad takes, silences, and adds captions using Soniox API for transcription and LLMs (Gemini) for take selection. Also includes screen recording with crop/aspect ratio selection.

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

Create a `.env` file in the project root (or use GUI Settings menu):

```bash
SONIOX_API_KEY=your_key_here     # Required for speech transcription (Soniox API)
GEMINI_API_KEY=your_key_here     # Required for intelligent take selection (Gemini 2.0 Flash)
```

API keys can also be configured via GUI: Menu → Settings. Keys are stored in `~/.video_editor_settings`.

## Architecture

### Core Pipeline (CLI)

The processing pipeline flows through four main components:

1. **Transcriber** (`video_editor/transcriber.py`) - Extracts audio via FFmpeg, uploads to Soniox API for Hungarian speech-to-text. Returns timestamped `Segment` and `Token` objects.

2. **Analyzer** (`video_editor/analyzer.py`) - Detects silences, identifies retakes using fuzzy matching (rapidfuzz), and selects best takes via LLM. Returns `TimeRange` objects marking segments to keep.

3. **Cutter** (`video_editor/cutter.py`) - Extracts segments using FFmpeg stream copy (no re-encoding for speed), then concatenates them.

4. **Captioner** (`video_editor/captioner.py`) - Generates SRT files and either burns captions into video (libx264) or adds soft subtitles.

### GUI Application

The GUI is built with PySide6 (Qt6) and provides two main tabs:

**Editor Tab:**
- **MainWindow** (`gui/main_window.py`) - Main application orchestrating all components
- **VideoPlayer** (`gui/video_player.py`) - QMediaPlayer-based video playback with crop/caption overlay
- **Timeline** (`gui/timeline.py`) - QGraphicsView-based segment visualization
- **TranscriptEditor** (`gui/transcript_editor.py`) - Text editing for transcription
- **CaptionSettingsPanel** (`gui/caption_settings.py`) - Caption styling configuration
- **SettingsDialog** (`gui/settings_dialog.py`) - API key management

**Recorder Tab:**
- **RecorderTab** (`gui/recorder/recorder_tab.py`) - Main recording interface
- **RecordingController** (`gui/recorder/recording_controller.py`) - FFmpeg-based screen/audio capture
- **RecordingPreview** (`gui/recorder/recording_preview.py`) - Live screen preview with crop overlay
- **RecordingSettingsPanel** (`gui/recorder/recording_settings.py`) - Recording configuration

### Key Data Structures

**Core (`transcriber.py`, `analyzer.py`):**
- `Segment` - Transcribed speech segment with start/end times, text, confidence
- `Token` - Word-level timing for streaming captions
- `TimeRange` - Start/end times for video cutting
- `RetakeGroup` - Multiple segments that are retakes of each other
- `AnalyzedSegment` - Segment with action (KEEP/REMOVE) and reason

**GUI (`gui/models.py`):**
- `EditSession` - Complete editing state (segments, tokens, user edits, highlights, crop, captions)
- `HighlightRegion` - User-defined force-include region for non-speech content
- `CropConfig` - Crop dimensions and pan position (normalized 0-1 coordinates)
- `CaptionSettings` - Caption font, position, box dimensions, styling options
- `RecordingConfig` - Screen capture settings, aspect ratio, audio device, output quality

### LLM Integration

Take selection uses Gemini 2.0 Flash (via `google-genai` package), falling back to duration-based selection if no API key is available.

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

User changes caption settings → CaptionSettingsPanel.settings_changed
                              → MainWindow._on_caption_settings_changed
                              → VideoPlayer.update_caption_settings
```

## FFmpeg Notes

- Audio extraction uses MP3 format for Soniox API upload
- Video cutting uses stream copy (`-c copy`) for speed
- Caption burning requires re-encoding with libx264
- Segment gap is 0.05s between cuts to avoid audio glitches
- Screen recording uses `avfoundation` on macOS with post-processing crop

## Common Tasks

### Adding a new timeline feature
1. Add data model to `gui/models.py` (with `to_dict`/`from_dict` for save/load)
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

### Adding a new recording feature
1. Update `RecordingConfig` in `gui/models.py` with new settings
2. Update `RecordingSettingsPanel` in `gui/recorder/recording_settings.py` for UI
3. Update `RecordingController` in `gui/recorder/recording_controller.py` for FFmpeg integration

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

- **API key errors**: Use GUI Settings menu to configure keys, or ensure `.env` file exists
- **GUI import errors**: Install PySide6: `pip install PySide6`
- **Export no captions**: Check tokens exist, segments are kept, and captions are enabled in settings panel
- **Recording crop mismatch**: The recorder applies a 50px margin to compensate for coordinate system differences between preview and capture
