# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-powered video editing tool for Hungarian spoken content. Automatically removes bad takes, silences, and adds captions using Whisper AI for transcription and LLMs (Gemini/OpenAI) for take selection.

## Common Commands

```bash
# Install dependencies
source venv/bin/activate
pip install -e .

# Run the video editor
python -m video_editor input.mp4 -o output.mp4

# Preview mode (show proposed cuts without processing)
python -m video_editor input.mp4 --preview

# Run with specific options
python -m video_editor input.mp4 --model medium --silence-threshold 1.5 --caption-style modern

# Run tests
pytest
```

## Environment Variables

- `GEMINI_API_KEY` - Primary LLM for take selection (Gemini 2.0 Flash)
- `OPENAI_API_KEY` - Fallback LLM for take selection
- `WHISPER_FORCE_CPU=1` - Force CPU for transcription (MPS/Apple Silicon has known issues)

## Architecture

The processing pipeline flows through four main components:

1. **Transcriber** (`transcriber.py`) - Extracts audio via FFmpeg, runs Whisper for Hungarian speech-to-text. Returns timestamped `Segment` objects.

2. **Analyzer** (`analyzer.py`) - Detects silences, identifies retakes using fuzzy matching (rapidfuzz), and selects best takes via LLM. Returns `TimeRange` objects marking segments to keep.

3. **Cutter** (`cutter.py`) - Extracts segments using FFmpeg stream copy (no re-encoding for speed), then concatenates them.

4. **Captioner** (`captioner.py`) - Generates SRT files and either burns captions into video (libx264) or adds soft subtitles.

### Key Data Structures

- `Segment` - Transcribed speech segment with start/end times, text, confidence
- `TimeRange` - Start/end times for video cutting
- `RetakeGroup` - Multiple segments that are retakes of each other (detected via text similarity)

### LLM Integration

Take selection prefers Gemini 2.0 Flash (via `google-genai` package), falls back to OpenAI, then to duration-based selection if no API keys are available.

## FFmpeg Notes

- Audio extraction uses 16kHz mono WAV (optimal for Whisper)
- Video cutting uses stream copy (`-c copy`) for speed
- Caption burning requires re-encoding with libx264
