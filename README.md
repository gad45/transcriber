# AI Video Editor

An AI-powered video editing tool for spoken content. Automatically removes bad takes, silences, and adds captions using Whisper AI for transcription and LLMs (Gemini/OpenAI) for intelligent take selection.

## Features

- **Automatic Transcription**: Uses Whisper AI for accurate speech-to-text (optimized for Hungarian)
- **Intelligent Take Selection**: LLM-powered detection of retakes, selecting the best version automatically
- **Silence Removal**: Configurable silence detection and removal
- **Streaming Captions**: Word-by-word captions burned into video
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

Create a `.env` file in the project root:

```bash
# Required for intelligent take selection (choose one or both)
GEMINI_API_KEY=your_gemini_api_key_here
OPENAI_API_KEY=your_openai_api_key_here

# Optional: Force CPU for Whisper (recommended for Apple Silicon)
WHISPER_FORCE_CPU=1
```

**Getting API Keys:**
- Gemini: https://makersuite.google.com/app/apikey
- OpenAI: https://platform.openai.com/api-keys

If no API keys are provided, the tool falls back to duration-based take selection.

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
    --model medium \
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

### Video Player
- Play/pause with spacebar
- Seek with left/right arrow keys (5 second jumps)
- Click timeline to seek to any position

### Timeline
- **Green segments**: Kept in final video
- **Red segments**: Cut from final video
- **Blue highlights**: User-defined force-include regions
- **White playhead**: Current playback position
- **Yellow border**: Retake candidates
- Ctrl+scroll to zoom in/out

### Transcript Editor
- View all transcribed segments
- Edit text to correct transcription errors
- Toggle keep/cut with checkbox or double-click
- Navigate with up/down arrow keys

### Highlight Regions
For screencasts or videos with important visual content without speech:
1. Click and drag on empty timeline space (minimum 0.5 seconds)
2. Blue highlight region appears
3. Right-click highlight to remove
4. Highlighted regions are force-included in export

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
- Resume editing later

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMINI_API_KEY` | Google Gemini API key for take selection | None |
| `OPENAI_API_KEY` | OpenAI API key (fallback) | None |
| `WHISPER_FORCE_CPU` | Force CPU for Whisper (set to `1`) | None |

### CLI Options

| Option | Description | Default |
|--------|-------------|---------|
| `--model` | Whisper model size (tiny/base/small/medium/large) | medium |
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
├── transcriber.py       # Whisper transcription
├── analyzer.py          # Retake detection & take selection
├── cutter.py            # FFmpeg video cutting
├── captioner.py         # Caption generation & burning
├── qc.py                # Quality control checks
└── gui/
    ├── __init__.py
    ├── main_window.py   # Main application window
    ├── video_player.py  # Video playback widget
    ├── timeline.py      # Timeline visualization
    ├── segment_item.py  # Timeline graphics items
    ├── transcript_editor.py  # Text editing panel
    └── models.py        # Data models for GUI state
```

### Processing Pipeline

1. **Transcription** (`transcriber.py`)
   - Extracts audio via FFmpeg (16kHz mono WAV)
   - Runs Whisper for speech-to-text
   - Returns timestamped segments and word-level tokens

2. **Analysis** (`analyzer.py`)
   - Detects silences in audio
   - Identifies retakes using fuzzy text matching (rapidfuzz)
   - Selects best takes via LLM (Gemini preferred, OpenAI fallback)
   - Falls back to duration-based selection if no API keys
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
- `EditSession`: GUI state including user edits and highlight regions
- `HighlightRegion`: User-defined force-include region

## Troubleshooting

### "SONIOX_API_KEY not set" or similar API errors
This error appears if environment variables aren't loaded. Ensure:
1. `.env` file exists in project root with your API keys
2. Running from project directory
3. Virtual environment is activated

### Whisper runs slowly on Apple Silicon
Set `WHISPER_FORCE_CPU=1` in your `.env` file. MPS (Metal) has known issues with Whisper.

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
- Try a larger Whisper model (`--model large`)
- Ensure the video has actual speech content

### Export has no captions
- Verify transcription completed successfully
- Check that segments are marked as "kept" (green)
- Ensure tokens exist in the session

### Video playback issues in GUI
- Ensure video codecs are supported by Qt
- Try converting to H.264 MP4 format first

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
