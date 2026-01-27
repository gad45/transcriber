# Video Editor - Session Handoff Document

## Session Date: January 26, 2026

## Issues Fixed This Session

### 1. Partial Words in Captions (Half-words Appearing)

**Symptom:** Captions showed partial words/syllables instead of complete words.

**Root Cause:** Soniox API returns tokens at syllable level (e.g., `'E'`, `'b'`, `'ben'` for the word `'Ebben'`).

**Solution:** Added `_merge_tokens_to_words()` in `transcriber.py` that merges syllable tokens into complete words:
- New word starts when token text begins with a space
- New word starts when there's a >0.3s gap between tokens

**Result:** Token count reduced from ~3000 syllables to ~1000 words, captions now display full words only.

---

### 2. Speech Duplication / Caption Desynchronization (CRITICAL)

**Symptoms Reported:**
- Speech/video content was being duplicated in the output
- Captions didn't follow the actual spoken content
- Captions kept going while video content repeated

**Root Causes Found:**

#### A. Imprecise Video Cutting (Primary Cause)
**Location:** `video_editor/cutter.py` - `cut_segment()` method

**Problem:** The original implementation used FFmpeg's stream copy (`-c copy`) which can only cut at keyframes. This caused segments to be much longer than requested:
- Requested 1.68s segment → Got 3.46s
- Requested 0.78s segment → Got 1.98s
- Requested 4.80s segment → Got 6.89s

The extra frames included speech from adjacent segments, causing the "duplication" effect.

**Solution:** Changed to re-encode video segments for frame-accurate cuts:
```python
# Before (imprecise):
cmd = [
    "ffmpeg", "-y",
    "-ss", str(start),
    "-i", str(input_path),
    "-t", str(end - start),
    "-c", "copy",  # Stream copy - only cuts at keyframes!
    ...
]

# After (precise):
cmd = [
    "ffmpeg", "-y",
    "-ss", str(start),
    "-i", str(input_path),
    "-t", str(end - start),
    "-c:v", "libx264",  # Re-encode for precise cuts
    "-preset", "fast",
    "-crf", "18",       # High quality
    "-c:a", "aac",
    "-b:a", "192k",
    ...
]
```

**Trade-off:** Re-encoding is slower than stream copy, but necessary for accurate timing.

#### B. Token Duplication Bug (Secondary Cause)
**Location:** `video_editor/main.py` - `_adjust_tokens_for_cuts()` function

**Problem:** The original nested loop structure processed every token for every keep range, potentially adding tokens multiple times:
```python
# Before (O(n × m) - tokens could be added multiple times):
for range_ in keep_ranges:
    for token in tokens:  # Re-iterates ALL tokens for EVERY range
        if range_.start <= token.start < range_.end:
            adjusted_tokens.append(...)
    current_offset += ...
```

**Solution:** Implemented single-pass algorithm where each token is processed exactly once:
```python
# After (O(n + m) - each token processed once):
sorted_tokens = sorted(tokens, key=lambda t: t.start)
sorted_ranges = sorted(keep_ranges, key=lambda r: r.start)

# Precompute cumulative offsets
offsets = []
cumulative = 0.0
for range_ in sorted_ranges:
    offsets.append(cumulative)
    cumulative += range_.duration

range_idx = 0
for token in sorted_tokens:
    # Skip ranges that end before this token
    while range_idx < len(sorted_ranges) and sorted_ranges[range_idx].end <= token.start:
        range_idx += 1

    # Check if token falls within current range
    if range_idx < len(sorted_ranges):
        range_ = sorted_ranges[range_idx]
        if range_.start <= token.start < range_.end:
            # Adjust and add token exactly once
            ...
```

---

## Results After Fixes

**Before fixes:**
- Final video: 427.9s (42.9% removed)
- Segments had wrong durations, speech was duplicated
- Captions desynchronized from audio

**After fixes:**
- Final video: 276.7s (63.1% removed)
- Segments have precise durations (within 0.04s of requested)
- Captions properly synchronized with speech

---

## Files Modified This Session

1. **`video_editor/cutter.py`** (lines 47-93)
   - Changed `cut_segment()` from stream copy to re-encoding
   - Ensures frame-accurate cuts at exact timestamps

2. **`video_editor/main.py`** (lines 25-81)
   - Rewrote `_adjust_tokens_for_cuts()` with single-pass algorithm
   - Added debug logging for token count before/after adjustment

3. **`video_editor/transcriber.py`** (lines 258-313)
   - Added `_merge_tokens_to_words()` method
   - Merges Soniox's syllable-level tokens into full words for caption display
   - Before: 3000+ syllable tokens showing partial words in captions
   - After: ~1000 word tokens showing complete words

---

## Previous Work (From Earlier Sessions)

### Soniox API Migration
- Migrated from Whisper to Soniox API for Hungarian transcription
- Soniox produces more granular segments (204 vs ~62 with Whisper)

### Retake Detection Improvements
The analyzer uses a 3-strategy approach for detecting retakes:

1. **Block-based detection** - Groups continuous speech into "recording blocks" separated by 3+ second pauses, compares block beginnings
2. **Within-block restart detection** - Catches short false starts using prefix similarity
3. **Traditional fuzzy matching** - For remaining segments within 60 seconds

**Location:** `video_editor/analyzer.py` - `detect_retakes()` method

### Streaming Captions
- Implemented word-by-word streaming captions using FFmpeg's `drawtext` filter
- Tokens are chunked by max words (default 20) and silence gaps
- Requires `ffmpeg-full` (installed via Homebrew) for `drawtext` filter support

---

## Architecture Overview

```
video_editor/
├── main.py          # CLI entry point, token adjustment
├── config.py        # Configuration and caption styles
├── transcriber.py   # Soniox API integration, audio extraction
├── analyzer.py      # Retake detection, silence detection, LLM take selection
├── cutter.py        # FFmpeg video cutting and concatenation
└── captioner.py     # SRT generation, caption burning (drawtext/ASS)
```

**Processing Pipeline:**
1. `Transcriber` - Extract audio → Soniox API → Segments + Tokens
2. `Analyzer` - Detect silences, find retakes, select best takes → Keep ranges
3. `Cutter` - Cut video segments → Concatenate
4. `_adjust_tokens_for_cuts()` - Remap token timestamps to new timeline
5. `Captioner` - Generate streaming captions → Burn into video

---

## Environment Requirements

```bash
# API Keys (in .env file)
SONIOX_API_KEY=...      # Required for transcription
GEMINI_API_KEY=...      # Primary LLM for take selection
OPENAI_API_KEY=...      # Fallback LLM

# FFmpeg with full filters
brew install ffmpeg     # Includes libfreetype for drawtext
```

---

## Testing Commands

```bash
# Full processing with streaming captions
python -m video_editor test/test.mp4 -o output.mp4 --streaming-captions

# Preview mode (no processing)
python -m video_editor test/test.mp4 --preview

# Keep temp files for debugging
python -m video_editor test/test.mp4 -o output.mp4 --streaming-captions --keep-temp
```

---

### 3. Black Gaps Between Segments

**Symptom:** Video went black/dark during transitions between cut segments.

**Root Cause:** Original implementation used `create_gap_segment()` which generated black frames with silence between segments.

**Solution:** Replaced black gaps with frozen last frame using FFmpeg's `tpad` filter:
```python
# In cut_segment() - freeze the last frame for SEGMENT_GAP duration
cmd = [
    "ffmpeg", "-y", "-i", str(temp_segment),
    "-vf", f"tpad=stop_mode=clone:stop_duration={self.SEGMENT_GAP}",
    ...
]
```

**Note:** `tpad` doesn't work well when combined with `-ss` seeking, so a two-pass approach is used:
1. Pass 1: Extract segment to temp file
2. Pass 2: Apply tpad to the extracted segment

---

### 4. Audio Gaps Between Segments

**Symptom:** Weird audio gaps/pops between segments after implementing frozen frame feature.

**Root Cause:** `apad=pad_dur=0.2` didn't produce audio that exactly matched video duration after `tpad` extended it. AAC encoding frame sizes caused slight mismatches (e.g., audio 1.885s vs video 1.9s).

**Solution:** Use generous audio padding with `-shortest` flag:
```python
cmd = [
    "ffmpeg", "-y", "-i", str(temp_segment),
    "-vf", f"tpad=stop_mode=clone:stop_duration={self.SEGMENT_GAP}",
    "-af", f"apad=pad_dur={self.SEGMENT_GAP + 0.5}",  # Generous padding
    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
    "-c:a", "aac", "-b:a", "192k",
    "-shortest",  # Trim both streams to match
    str(output_path)
]
```

This ensures audio is padded more than needed, then FFmpeg trims both streams to match.

---

### 5. Token Adjustment for Segment Gaps

**Location:** `video_editor/main.py` - `_adjust_tokens_for_cuts()`

**Change:** Updated to account for frozen frame gaps between segments when computing caption timestamps:
```python
# Precompute cumulative offsets including gaps
for i, range_ in enumerate(sorted_ranges):
    offsets.append(cumulative)
    cumulative += range_.duration
    # Add gap after each segment except the last
    if i < len(sorted_ranges) - 1:
        cumulative += segment_gap  # 0.2s gap
```

---

## Files Modified This Session (Updated)

1. **`video_editor/cutter.py`**
   - Added `SEGMENT_GAP = 0.2` class constant
   - Changed `cut_segment()` to two-pass approach with frozen last frame
   - Added `freeze_last_frame` parameter (disabled for final segment)
   - Fixed audio gap issue with generous padding + `-shortest`
   - Simplified `concatenate_segments()` (no separate gap segments needed)

2. **`video_editor/main.py`**
   - Updated `_adjust_tokens_for_cuts()` to account for segment gaps

3. **`video_editor/transcriber.py`**
   - Added `_merge_tokens_to_words()` method

4. **`video_editor/captioner.py`**
   - Updated to use two separate drawtext filters for 2-line caption layout

---

## Known Trade-offs

1. **Re-encoding vs Speed**: Frame-accurate cuts require re-encoding, which is slower than stream copy but necessary for caption synchronization.

2. **Soniox Granularity**: More segments means better precision but requires robust retake detection algorithms.

3. **LLM Take Selection**: Uses Gemini 2.0 Flash (primary) or OpenAI (fallback) for selecting best takes. Falls back to duration-based selection if no API keys available.

4. **Two-Pass Segment Processing**: Frozen frame feature requires two FFmpeg passes per segment (extract, then tpad), adding processing time but ensuring smooth transitions.

---

## Session Date: January 27, 2026

### 6. Transcription Quality Control (QC) Agent

**Feature:** Added LLM-based quality control for Hungarian transcription.

**Location:** `video_editor/qc.py` (new file)

**Implementation:**
- Uses Gemini 2.0 Flash to validate transcribed text
- Checks semantic coherence, Hungarian grammar, and common ASR errors
- Batch processes segments (10 per API call) for efficiency
- Can auto-correct errors or just report them

**CLI Options:**
- `--skip-qc` - Skip quality control entirely
- `--qc-report-only` - Run QC but don't auto-correct

**Config Options:**
- `qc_enabled` - Enable/disable QC (default: True)
- `qc_auto_correct` - Auto-apply corrections (default: True)
- `qc_model` - Gemini model to use (default: "gemini-2.0-flash")

---

### 7. Word Cutoff Fix (Timing Buffers)

**Symptom:** Words were being cut off at the beginning or end of segments (~0.1s too early).

**Root Cause:** Segment boundaries from Soniox token timestamps were used exactly without any buffer. Soniox timing represents acoustic onset, not when words are fully audible.

**Solution:** Added configurable timing buffers in `config.py`:
```python
segment_start_buffer: float = 0.1   # 100ms before segment start
segment_end_buffer: float = 0.15    # 150ms after segment end
caption_delay: float = 0.1          # 100ms delay for caption appearance
```

**Implementation:**
- `analyzer.py` - Apply buffers when creating keep_ranges
- `analyzer.py` - Added `_merge_overlapping_ranges()` to merge adjacent segments after buffering
- `captioner.py` - Apply caption_delay to word timing in drawtext filter

**Result:**
- Words no longer cut off at segment boundaries
- Captions appear in better sync with spoken words
- Adjacent segments merged to avoid duplicate content (124 segments → 35 merged segments)

---

## Files Modified (January 27, 2026)

1. **`video_editor/qc.py`** (NEW)
   - QualityController class for Hungarian transcription validation
   - Uses Gemini API for grammar/semantic checking
   - Batch processing with JSON response parsing

2. **`video_editor/config.py`**
   - Added QC settings (qc_enabled, qc_auto_correct, qc_model)
   - Added timing buffer settings (segment_start_buffer, segment_end_buffer, caption_delay)

3. **`video_editor/analyzer.py`**
   - Apply timing buffers to keep_ranges
   - Added `_merge_overlapping_ranges()` method

4. **`video_editor/captioner.py`**
   - Apply caption_delay to word timing

5. **`video_editor/main.py`**
   - Integrated QC step into pipeline
   - Added --skip-qc and --qc-report-only CLI options
   - Simplified step counting

---

## Current Pipeline Steps

1. **Get video info** - Duration check
2. **Transcribe** - Soniox API → Segments + Tokens
3. **Quality Control** - Gemini validates/corrects Hungarian text (optional)
4. **Analyze** - Detect silences, retakes, select best takes → Keep ranges (with buffers)
5. **Process** - Cut video, add captions (with delay)

---

## Testing Commands (Updated)

```bash
# Full processing with QC and streaming captions
python -m video_editor test/test.mp4 -o output.mp4 --streaming-captions

# Skip QC for faster processing
python -m video_editor test/test.mp4 -o output.mp4 --streaming-captions --skip-qc

# QC report only (no auto-corrections)
python -m video_editor test/test.mp4 -o output.mp4 --streaming-captions --qc-report-only

# Launch GUI
python -m video_editor.gui_main test/test.mp4
```

---

### 8. Graphical User Interface (GUI)

**Feature:** Added PySide6-based GUI for interactive editing.

**Location:** `video_editor/gui/` (new package)

**Purpose:**
- Edit transcript text manually (for words AI misrecognizes)
- See before/after of video edit on a visual timeline
- Re-enable cut segments that were incorrectly removed

**Key Components:**

```
video_editor/gui/
├── __init__.py           # Package exports
├── main_window.py        # Main application window
├── video_player.py       # Video playback with QMediaPlayer
├── timeline.py           # Visual timeline with segment blocks
├── transcript_editor.py  # Editable text for each segment
├── segment_item.py       # Timeline segment graphics items
└── models.py             # EditSession data model with save/load
```

**Features:**
- **Video Player**: Play, pause, seek with keyboard shortcuts (Space, Left/Right arrows)
- **Timeline**: Color-coded segments (green=keep, red=cut), click to seek, right-click to toggle
- **Transcript Editor**: Editable text per segment, checkbox to toggle keep/cut
- **Project Files**: Save/load editing sessions as `.vedproj` JSON files
- **Keyboard Shortcuts**:
  - Space: Play/Pause
  - Left/Right: Jump ±5 seconds
  - Up/Down: Previous/Next segment
  - K: Toggle current segment keep/cut
  - Ctrl+S: Save project

**GUI Entry Points:**
```bash
# Module invocation
python -m video_editor.gui_main test/test.mp4

# After pip install
video-editor-gui test/test.mp4
```

**Workflow:**
1. Load video → Click "Analyze Video" to run transcription + analysis
2. Review segments in timeline and transcript editor
3. Toggle keep/cut for segments that were incorrectly classified
4. Edit transcript text for misrecognized words
5. Save project (Ctrl+S) to resume later
6. Export final video

---

## Files Added (January 27, 2026 - GUI)

1. **`video_editor/gui/__init__.py`** - Package exports
2. **`video_editor/gui/main_window.py`** - Main application window with menu, toolbar, layout
3. **`video_editor/gui/video_player.py`** - QMediaPlayer-based video playback widget
4. **`video_editor/gui/timeline.py`** - QGraphicsView-based timeline with zoom
5. **`video_editor/gui/transcript_editor.py`** - Scrollable list of editable segments
6. **`video_editor/gui/segment_item.py`** - QGraphicsRectItem for timeline segments
7. **`video_editor/gui/models.py`** - EditSession class with save/load JSON
8. **`video_editor/gui_main.py`** - GUI entry point

## Dependencies Added
- `PySide6>=6.6.0` - Qt6 bindings for Python GUI

---

## Architecture Overview (Updated)

```
video_editor/
├── main.py          # CLI entry point
├── gui_main.py      # GUI entry point (NEW)
├── gui/             # GUI package (NEW)
│   ├── main_window.py
│   ├── video_player.py
│   ├── timeline.py
│   ├── transcript_editor.py
│   ├── segment_item.py
│   └── models.py
├── config.py        # Configuration and caption styles
├── transcriber.py   # Soniox API integration, audio extraction
├── analyzer.py      # Retake detection, silence detection, LLM take selection
├── cutter.py        # FFmpeg video cutting and concatenation
├── captioner.py     # SRT generation, caption burning (drawtext/ASS)
└── qc.py            # Transcription quality control
```
