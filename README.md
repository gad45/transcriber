# AI Video Editor

An AI-powered video editing tool for spoken content. Automatically removes bad takes, silences, and adds captions using Soniox API for transcription and LLMs (Gemini/OpenAI) for intelligent take selection.

## Features

- **Automatic Transcription**: Uses Soniox API for accurate speech-to-text (optimized for Hungarian)
- **Intelligent Take Selection**: LLM-powered detection of retakes, selecting the best version automatically
- **Silence Removal**: Configurable silence detection and removal
- **Streaming Captions**: Word-by-word captions burned into video with draggable positioning
- **Video Cropping & Panning**: Interactive crop selection with aspect ratio constraints
- **Screen Recording**: Built-in screen capture with aspect ratio selection and audio input
- **GUI Editor**: Full graphical interface for reviewing and adjusting edits
- **Timeline Highlights**: Mark non-speech regions to force-include (for screencasts)
- **Project Files**: Save/load editing sessions for later refinement

## Requirements

- Python 3.10+
- FFmpeg (must be installed and in PATH)
- macOS, Linux, or Windows

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd FFMPEG
```

### 2. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -e .
```

### 4. Install FFmpeg

**macOS (Homebrew):**
```bash
brew install ffmpeg
```

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install ffmpeg
```

**Windows:**
Download from https://ffmpeg.org/download.html and add to PATH, or use:
```bash
choco install ffmpeg
```

### 5. Set Up API Keys

You can configure API keys via the GUI Settings menu, or create a `.env` file in the project root:

```bash
# Required for speech transcription
SONIOX_API_KEY=your_soniox_api_key_here

# Required for intelligent take selection (recommended)
GEMINI_API_KEY=your_gemini_api_key_here
```

**Getting API Keys:**
- Soniox: https://soniox.com
- Gemini: https://makersuite.google.com/app/apikey

If no Gemini API key is provided, the tool falls back to duration-based take selection.

## Usage

### Command Line Interface

**Basic usage:**
```bash
python -m video_editor input.mp4 -o output.mp4
```

**Preview mode (show proposed cuts without processing):**
```bash
python -m video_editor input.mp4 --preview
```

**With options:**
```bash
python -m video_editor input.mp4 \
    --output output.mp4 \
    --silence-threshold 1.5 \
    --caption-style modern
```

**Without captions:**
```bash
python -m video_editor input.mp4 -o output.mp4 --no-captions
```

**Soft subtitles (selectable/toggleable):**
```bash
python -m video_editor input.mp4 -o output.mp4 --soft-captions
```

### Graphical User Interface

**Launch the GUI:**
```bash
python -m video_editor.gui_main
```

**Or with a video file:**
```bash
python -m video_editor.gui_main path/to/video.mp4
```

**macOS Quick Launch:**
Double-click `launch_gui.command` in Finder.

**Windows Quick Launch:**
Double-click `launch_gui.bat` in Explorer.

## GUI Features

### Editor Tab

#### Video Player
- Play/pause with spacebar
- Seek with left/right arrow keys (5 second jumps)
- Click timeline to seek to any position

#### Timeline
- **Green segments**: Kept in final video
- **Red segments**: Cut from final video
- **Blue highlights**: User-defined force-include regions
- **White playhead**: Current playback position
- **Yellow border**: Retake candidates
- Ctrl+scroll to zoom in/out

#### Transcript Editor
- View all transcribed segments
- Edit text to correct transcription errors
- Toggle keep/cut with checkbox or double-click
- Navigate with up/down arrow keys

#### Video Cropping
Adjust the frame to focus on specific areas:
1. Click "Crop" button in toolbar to enter crop mode
2. Select aspect ratio (16:9, 9:16, 4:3, 1:1, or free)
3. Drag to create crop region, or drag edges/corners to adjust
4. Drag inside the crop to pan/move
5. Click "Crop" again to exit and preview the result
6. Crop is applied during export

#### Caption Styling
Customize how captions appear in the video:
1. Click "Captions" button (available after analysis)
2. Toggle "Enable captions" to show/hide in preview and export
3. Adjust font size, font family, weight, and colors
4. Click "Drag to Move" to reposition the caption box
5. Drag the caption box edges/corners to resize
6. Click "Reset Position" to restore defaults

#### Highlight Regions
For screencasts or videos with important visual content without speech:
1. Click and drag on empty timeline space (minimum 0.5 seconds)
2. Blue highlight region appears
3. Right-click highlight to remove
4. Highlighted regions are force-included in export

### Recorder Tab

Built-in screen recording with:
- Live preview of screen being captured
- Aspect ratio selection (16:9, 9:16, 4:3, 1:1, or full screen)
- Draggable crop overlay for region selection
- Audio device selection and volume control
- Record/Stop/Pause controls
- Automatic transition to editor after recording

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Space | Play/Pause |
| Left Arrow | Jump back 5 seconds |
| Right Arrow | Jump forward 5 seconds |
| Up Arrow | Previous segment |
| Down Arrow | Next segment |
| K | Toggle keep/cut for selected segment |
| Ctrl+S | Save project |
| Ctrl+E | Export video |
| Ctrl+O | Open video |
| Ctrl+Shift+O | Open project |

### Project Files

Save your editing session as a `.vedproj` file to:
- Preserve all text edits
- Keep segment keep/cut decisions
- Store highlight regions
- Store crop and caption settings
- Resume editing later

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SONIOX_API_KEY` | Soniox API key for transcription | None (required) |
| `GEMINI_API_KEY` | Google Gemini API key for take selection | None (optional) |

### CLI Options

| Option | Description | Default |
|--------|-------------|---------|
| `--silence-threshold` | Minimum silence duration to remove (seconds) | 1.5 |
| `--retake-similarity` | Text similarity threshold for retake detection (0-1) | 0.8 |
| `--caption-style` | Caption style (minimal/modern/bold) | modern |
| `--no-captions` | Disable caption burning | False |
| `--soft-captions` | Add selectable subtitles instead of burning | False |
| `--preview` | Preview cuts without processing | False |

### Internal Config

Edit `video_editor/config.py` for additional options:

| Option | Description | Default |
|--------|-------------|---------|
| `segment_start_buffer` | Buffer before speech starts (seconds) | 0.1 |
| `segment_end_buffer` | Buffer after speech ends (seconds) | 0.15 |
| `max_caption_words` | Maximum words per caption line | 6 |

## Architecture

```
video_editor/
├── __init__.py
├── main.py              # CLI entry point
├── config.py            # Configuration management
├── transcriber.py       # Soniox API transcription
├── analyzer.py          # Retake detection & take selection
├── cutter.py            # FFmpeg video cutting
├── captioner.py         # Caption generation & burning
├── qc.py                # Quality control checks
└── gui/
    ├── __init__.py
    ├── main_window.py       # Main application window
    ├── video_player.py      # Video playback with crop/caption overlay
    ├── timeline.py          # Timeline visualization
    ├── segment_item.py      # Timeline graphics items
    ├── transcript_editor.py # Text editing panel
    ├── caption_settings.py  # Caption styling panel
    ├── settings_dialog.py   # API key configuration
    ├── models.py            # Data models (EditSession, CropConfig, CaptionSettings)
    └── recorder/            # Screen recording module
        ├── recorder_tab.py
        ├── recording_controller.py
        ├── recording_preview.py
        └── recording_settings.py
```

### Processing Pipeline

1. **Transcription** (`transcriber.py`)
   - Extracts audio via FFmpeg (MP3)
   - Uploads to Soniox API for speech-to-text
   - Returns timestamped segments and word-level tokens

2. **Analysis** (`analyzer.py`)
   - Detects silences in audio
   - Identifies retakes using fuzzy text matching (rapidfuzz)
   - Selects best takes via LLM (Gemini)
   - Falls back to duration-based selection if no API key
   - Returns time ranges to keep

3. **Cutting** (`cutter.py`)
   - Extracts segments using FFmpeg stream copy (fast, no re-encoding)
   - Concatenates segments into final video

4. **Captioning** (`captioner.py`)
   - Generates SRT subtitle files
   - Burns word-by-word streaming captions using libx264

### Key Data Structures

- `Segment`: Transcribed speech segment with start/end times, text, confidence
- `Token`: Word-level timing for streaming captions
- `TimeRange`: Start/end times for video cutting
- `RetakeGroup`: Multiple segments that are retakes of each other
- `EditSession`: GUI state including user edits, crops, and highlights
- `HighlightRegion`: User-defined force-include region
- `CropConfig`: Crop dimensions and pan position (normalized 0-1)
- `CaptionSettings`: Caption font, position, and box dimensions
- `RecordingConfig`: Screen capture settings, aspect ratio, audio device

## Troubleshooting

### "SONIOX_API_KEY not set" error
Configure your API key via GUI Settings menu or ensure `.env` file exists with your key.

### FFmpeg not found
Ensure FFmpeg is installed and in your PATH:
```bash
ffmpeg -version
```

### GUI won't launch
Install PySide6:
```bash
pip install PySide6
```

### No speech detected
- Check audio levels in source video
- Ensure the video has actual speech content
- Verify Soniox API key is valid

### Export has no captions
- Verify transcription completed successfully
- Check that segments are marked as "kept" (green)
- Ensure "Enable captions" is checked in Caption Settings panel
- Ensure tokens exist in the session

### Video playback issues in GUI
- Ensure video codecs are supported by Qt
- Try converting to H.264 MP4 format first

### Recording crop doesn't match preview
The recorder applies a 50px margin to compensate for coordinate system differences between preview and capture. This is expected behavior.

## Development

### Running Tests
```bash
pytest
```

### Project Structure
- CLI: `python -m video_editor`
- GUI: `python -m video_editor.gui_main`
- Tests: `pytest tests/`

### Adding New Features

The GUI uses PySide6 (Qt6) with:
- `QGraphicsView` for timeline visualization
- `QMediaPlayer` for video playback
- Signal/Slot pattern for component communication
- `EditSession` model for tracking user modifications

### Using Claude Code for Development

This project includes a `CLAUDE.md` file that provides context for AI-assisted development. When working with Claude Code:

1. The agent understands the project architecture
2. Common commands are documented
3. Environment setup is explained

To continue development with Claude Code:
```bash
claude
```

Then describe what you want to build or fix.

## License

MIT

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `pytest`
5. Submit a pull request
