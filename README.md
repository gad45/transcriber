# AI Video Editing Agent

Automatically edit Hungarian spoken content videos by removing bad takes, pauses, and adding captions.

## Features

- 🎙️ **Hungarian Speech Transcription** - Uses OpenAI Whisper for accurate Hungarian speech-to-text
- ✂️ **Automatic Bad Take Detection** - Identifies and removes retakes using fuzzy text matching
- 🤖 **AI-Powered Take Selection** - Uses LLM to pick the best take from multiple versions
- 🔇 **Silence Removal** - Automatically cuts long pauses and dead air
- 📝 **Caption Generation** - Generates and burns Hungarian captions into the video
- ⚡ **FFmpeg Powered** - Fast, reliable video processing

## Requirements

- Python 3.10+
- FFmpeg installed and available in PATH
- (Optional) OpenAI API key for AI-powered take selection

## Installation

```bash
cd /Users/gorogadam/project/FFMPEG

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .

# Or with requirements.txt
pip install -r requirements.txt
```

### FFmpeg Installation

**macOS:**
```bash
brew install ffmpeg
```

**Ubuntu/Debian:**
```bash
sudo apt update && sudo apt install ffmpeg
```

**Windows:**
```bash
choco install ffmpeg
```

## Usage

### Basic Usage

```bash
# Process a video with default settings
python -m video_editor input.mp4 -o output.mp4

# Or if installed
video-editor input.mp4 -o output.mp4
```

### Options

```bash
python -m video_editor input.mp4 \
  --model medium \           # Whisper model (tiny/base/small/medium/large)
  --silence-threshold 1.5 \  # Min silence to cut (seconds)
  --retake-similarity 0.8 \  # Retake detection threshold (0-1)
  --caption-style modern \   # Caption style (minimal/modern/bold)
  --preview                  # Preview cuts without processing
```

### Preview Mode

See what would be cut before processing:

```bash
python -m video_editor input.mp4 --preview
```

### Without Captions

```bash
python -m video_editor input.mp4 -o output.mp4 --no-captions
```

### Soft Subtitles (Selectable)

```bash
python -m video_editor input.mp4 -o output.mp4 --soft-captions
```

### Using AI Take Selection

Set your OpenAI API key:

```bash
export OPENAI_API_KEY="your-key-here"
python -m video_editor input.mp4 -o output.mp4
```

Or pass directly:

```bash
python -m video_editor input.mp4 --openai-key "your-key-here"
```

## How It Works

1. **Audio Extraction** - FFmpeg extracts audio from video
2. **Transcription** - Whisper transcribes Hungarian speech with timestamps
3. **Analysis** - Detects silences, retakes, and bad takes
4. **Take Selection** - LLM evaluates retakes and selects the best one
5. **Cutting** - FFmpeg cuts video based on analysis
6. **Captioning** - Generates SRT and burns captions into video

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `--model` | medium | Whisper model size (larger = more accurate) |
| `--silence-threshold` | 1.5 | Min silence duration to cut (seconds) |
| `--retake-similarity` | 0.8 | Text similarity threshold for retakes |
| `--caption-style` | modern | minimal, modern, or bold |

## License

MIT
