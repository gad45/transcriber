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
5. Adjust crop/pan and caption styling as needed
6. Save project (Ctrl+S) to resume later
7. Export final video

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
├── gui_main.py      # GUI entry point
├── gui/             # GUI package
│   ├── main_window.py
│   ├── video_player.py
│   ├── timeline.py
│   ├── transcript_editor.py
│   ├── segment_item.py
│   ├── caption_settings.py  # Caption styling panel (NEW)
│   └── models.py            # Includes CropConfig, CaptionSettings
├── config.py        # Configuration and caption styles
├── transcriber.py   # Soniox API integration, audio extraction
├── analyzer.py      # Retake detection, silence detection, LLM take selection
├── cutter.py        # FFmpeg video cutting and concatenation
├── captioner.py     # SRT generation, caption burning (drawtext/ASS)
└── qc.py            # Transcription quality control
```

---

## Session Date: January 27, 2026 (Continued)

### 9. Video Cropping and Panning

**Feature:** Interactive crop selection with mouse-based adjustment.

**Location:** `video_editor/gui/video_player.py`, `video_editor/gui/models.py`

**Implementation:**
- `CropConfig` dataclass stores normalized (0-1) crop dimensions and pan position
- `VideoView` class handles mouse events for crop selection and adjustment
- Crop overlay shows semi-transparent mask in edit mode, opaque mask in preview mode
- Supports aspect ratio constraints (16:9, 9:16, 4:3, 1:1, free)
- Drag edges/corners to resize, drag inside to move/pan
- Per-segment crop overrides supported

**Key Methods:**
- `set_crop_mode(enabled)` - Toggle crop editing mode
- `set_crop_config(config)` - Apply crop settings
- `_apply_crop_view()` - Show crop preview with opaque mask
- `_update_crop_overlay()` - Update crop rectangles in edit mode

**Export Integration:**
- Crop is converted to FFmpeg filter string via `CropConfig.to_ffmpeg_filter()`
- Applied during video cutting in `cutter.py`

---

### 10. Draggable Caption Box with Fixed Size

**Feature:** Caption box with fixed dimensions, word wrapping, and drag-to-move/resize.

**Location:** `video_editor/gui/video_player.py`, `video_editor/gui/caption_settings.py`, `video_editor/gui/models.py`

**Implementation:**
- `CaptionSettings` dataclass stores:
  - `font_size`, `font_family` - Text styling
  - `pos_x`, `pos_y` - Normalized position (center-x, bottom-y)
  - `box_width`, `box_height` - Normalized box dimensions (default 60% x 7%)
  - `show_preview` - Toggle caption visibility
- Caption box maintains fixed size regardless of text content
- Text wraps within the box using `QGraphicsTextItem.setTextWidth()`
- Drag center to move, drag edges/corners to resize

**Key Methods:**
- `update_caption(time_seconds)` - Display caption at current time with fixed box
- `set_caption_mode(enabled)` - Toggle drag interaction mode
- `_on_caption_rect_changed()` - Live preview during drag
- `_on_caption_drag_finished()` - Save normalized position/size to settings

**CaptionSettingsPanel Widget:**
- Font size slider and font family dropdown
- "Drag to Move" toggle button
- Position/size display (read-only)
- "Reset Position" button

---

### 11. Crop Preview Fix

**Problem:** After exiting crop mode, the preview showed parts of the video that wouldn't be in the final export.

**Root Cause:** `fitInView()` with `KeepAspectRatio` centers the crop region but doesn't clip content outside it.

**Solution:** Use fully opaque black overlay rectangles to mask non-cropped areas in preview mode:
- In crop mode: Semi-transparent overlays (alpha 160) for editing visibility
- In preview mode: Fully opaque overlays (alpha 255) to accurately show final result

---

## Files Modified (January 27, 2026 - Crop & Caption)

1. **`video_editor/gui/models.py`**
   - Added `CropConfig` dataclass with normalized dimensions
   - Updated `CaptionSettings` with `pos_x`, `pos_y`, `box_width`, `box_height`
   - Added `get_box_pixels()` method for fixed-size caption box

2. **`video_editor/gui/video_player.py`**
   - Added `VideoView` class with crop/caption mouse interaction
   - Added crop overlay rectangles and mode switching
   - Added caption overlay with fixed-size box and word wrapping
   - Added `_hit_test_caption()` for move vs resize detection

3. **`video_editor/gui/caption_settings.py`** (NEW)
   - `CaptionSettingsPanel` widget for font/position controls
   - "Drag to Move" toggle and "Reset Position" button

4. **`video_editor/gui/main_window.py`**
   - Added crop toolbar (Crop button, aspect ratio dropdown, Reset)
   - Added caption toolbar integration
   - Connected crop/caption signals and handlers

5. **`video_editor/config.py`**
   - Added `caption_position` and `caption_vertical_offset` for CLI export

---

## Session Date: January 31, 2026

### 12. Screen and Audio Recording Feature

**Feature:** Added a separate "Recorder" tab for screen and audio recording directly in the video editor.

**Location:** `video_editor/gui/recorder/` (new package)

**Purpose:**
- Record screen content with optional aspect ratio cropping
- Capture audio from selected input device
- Preview the recording area before starting
- See live audio level feedback during setup

**Key Components:**

```
video_editor/gui/recorder/
├── __init__.py               # Package exports
├── recording_controller.py   # Core recording logic with Qt multimedia
├── recorder_tab.py           # Main UI combining all components
├── recording_preview.py      # Live preview widget with crop overlay
├── recording_settings.py     # Settings panel (screen, audio, quality)
└── audio_level_meter.py      # Visual audio level indicator
```

**RecordingConfig Dataclass:**
```python
@dataclass
class RecordingConfig:
    screen_index: int = 0
    capture_full_screen: bool = True
    target_aspect_ratio: tuple[int, int] | None = None
    crop_offset_x: float = 0.5  # Normalized 0-1
    crop_offset_y: float = 0.5  # Normalized 0-1
    audio_device_id: str = ""
    audio_enabled: bool = True
    audio_volume: float = 1.0
    output_directory: str = ""
    video_quality: str = "high"
    audio_sample_rate: int = 48000
```

**Features Implemented:**

1. **Screen Recording** - Uses `QScreenCapture` + `QMediaRecorder` to record screen
2. **Live Preview** (WORKING) - Screen capture activates on tab show for real-time preview
3. **Draggable Crop Overlay** - Position recording region for specific aspect ratios
4. **Audio Device Selection** - List and select from available input devices
5. **Audio Level Meter** (WORKING) - Real-time VU meter using `QAudioSource` for monitoring
6. **Pause/Resume** - Pause and resume recording
7. **FFmpeg Post-Processing** - Crop filter applied after recording for aspect ratio

**Technical Implementation:**

- **Preview System**: `start_preview()` / `stop_preview()` methods control screen capture independently of recording
- **Audio Monitoring**: Separate `QAudioSource` for level metering (16kHz mono, 20Hz update rate)
- **RMS Level Calculation**: Reads 16-bit audio samples, calculates RMS, normalizes to 0.0-1.0
- **Tab Lifecycle**: `showEvent()` starts preview, `hideEvent()` stops it to save resources

---

### 13. Audio Recording Fix (January 31, 2026 - Continued)

**Problem:** Audio was not being recorded, and the audio level meter wasn't responding.

**Root Cause:** macOS requires microphone permission granted to the **Terminal app** (not the Python script). Qt's `QMicrophonePermission` API doesn't properly detect Terminal's permission status.

**Solution:**
1. Added `QMicrophonePermission` check with fallback - if status is "Denied", proceed anyway since Terminal may have access
2. Added `_permission_checked` flag to avoid repeated permission checks
3. Added diagnostic logging throughout audio setup

**Audio Quality Improvements:**
- Changed quality from `HighQuality` to `VeryHighQuality`
- Set explicit audio settings:
  - Sample rate: 48000 Hz (professional standard)
  - Channels: 2 (stereo)
  - Bit rate: 256 kbps (high quality AAC)

**Key Code Changes in `recording_controller.py`:**
```python
# High-quality audio settings
self._recorder.setQuality(QMediaRecorder.Quality.VeryHighQuality)
self._recorder.setAudioSampleRate(48000)
self._recorder.setAudioChannelCount(2)
self._recorder.setAudioBitRate(256000)
```

**macOS Permission Note:** Users must grant microphone access to Terminal (or their terminal app) in System Settings > Privacy & Security > Microphone.

---

### 14. Raw Recording Preservation (January 31, 2026 - Continued)

**Problem:** If the app crashed during post-processing (cropping), the raw recording could be lost.

**Solution:** Raw recordings are now saved to a dedicated subdirectory and **never deleted**.

**New File Structure:**
```
~/Movies/Recordings/
├── raw/                              # Raw recordings - NEVER deleted
│   ├── recording_20260131_123456.mp4
│   └── recording_20260131_124530.mp4
├── recording_20260131_123456.mp4     # Cropped versions (if cropping applied)
└── recording_20260131_124530.mp4
```

**Safety Guarantees:**
1. Raw files saved to `~/Movies/Recordings/raw/` subdirectory
2. App never deletes raw recordings automatically
3. Crash-safe: raw file is on disk before any post-processing begins
4. Clear messaging shows both raw and processed file locations

**Key Code Changes:**
- `recording_controller.py`: `_get_output_path()` now saves to `raw/` subdirectory
- `recorder_tab.py`: `_process_crop()` saves cropped file to parent directory, keeps raw file

---

## Files Added (January 31, 2026 - Recorder)

1. **`video_editor/gui/recorder/__init__.py`** - Package exports
2. **`video_editor/gui/recorder/recording_controller.py`** - Core recording logic
   - `RecordingController` class wrapping Qt multimedia
   - `start_preview()` / `stop_preview()` for live preview
   - `_start_audio_monitoring()` / `_update_audio_level()` for level meter
   - `RecordingState` enum (IDLE, RECORDING, PAUSED, PROCESSING)
3. **`video_editor/gui/recorder/recorder_tab.py`** - Main recorder UI
   - Toolbar with Record/Stop/Pause buttons
   - Splitter layout with preview and settings
   - `showEvent()` / `hideEvent()` for preview lifecycle
4. **`video_editor/gui/recorder/recording_preview.py`** - Live preview widget
   - `QGraphicsView` with `QGraphicsVideoItem`
   - Draggable crop overlay for aspect ratio selection
5. **`video_editor/gui/recorder/recording_settings.py`** - Settings panel
   - Screen selection dropdown
   - Aspect ratio selection
   - Audio device dropdown with level meter
   - Volume slider
6. **`video_editor/gui/recorder/audio_level_meter.py`** - VU meter widget
   - Custom `paintEvent` with green/yellow/red gradient
   - Animated level display

## Files Modified (January 31, 2026 - Recorder)

1. **`video_editor/gui/models.py`**
   - Added `RecordingConfig` dataclass
   - Methods: `get_crop_rect()`, `to_ffmpeg_crop_filter()`, `to_dict()`, `from_dict()`

2. **`video_editor/gui/main_window.py`**
   - Changed from single widget to `QTabWidget`
   - Added "Editor" and "Recorder" tabs
   - Tab change handler pauses video when switching away

3. **`video_editor/gui/__init__.py`**
   - Added exports for `RecorderTab`, `RecordingController`

---

## Architecture Overview (Updated)

```
video_editor/
├── main.py              # CLI entry point
├── gui_main.py          # GUI entry point
├── gui/
│   ├── main_window.py   # Tab-based main window (Editor + Recorder)
│   ├── video_player.py
│   ├── timeline.py
│   ├── transcript_editor.py
│   ├── segment_item.py
│   ├── caption_settings.py
│   ├── models.py        # EditSession, RecordingConfig, CropConfig, CaptionSettings
│   └── recorder/        # NEW - Screen recording package
│       ├── recording_controller.py
│       ├── recorder_tab.py
│       ├── recording_preview.py
│       ├── recording_settings.py
│       └── audio_level_meter.py
├── config.py
├── transcriber.py
├── analyzer.py
├── cutter.py
├── captioner.py
└── qc.py
```

---

## Current Status (January 31, 2026)

**Working Features:**
- Screen recording with optional aspect ratio cropping
- Audio recording with high quality (48kHz, stereo, 256kbps AAC)
- Live screen preview before recording
- Audio device selection
- Raw file preservation (crash-safe)

**Known Limitations:**
- Audio level meter may not respond (Qt permission API issue with Terminal)
- Users must manually grant Terminal microphone access in System Settings
- Raw files accumulate in `~/Movies/Recordings/raw/` - manual cleanup needed

---

## Session Date: January 31, 2026 (Continued)

### 15. Fixed Resolution Recording Support

**Feature:** Added support for fixed resolution recording presets (e.g., 1920x1080) in addition to aspect ratios.

**Problem:** Previously, the recorder only supported aspect ratios (16:9, 9:16) that scaled to fill the screen height. Users wanted to record a specific resolution like 1920x1080.

**Solution:** Added `target_resolution` field to `RecordingConfig` and updated UI with resolution presets.

**New Crop Presets:**
```python
CROP_PRESETS = [
    ("Full Screen", None, None),
    ("1920x1080 (1080p)", (1920, 1080), None),
    ("1280x720 (720p)", (1280, 720), None),
    ("1080x1920 (Vertical 1080p)", (1080, 1920), None),
    ("720x1280 (Vertical 720p)", (720, 1280), None),
    ("1080x1080 (Square)", (1080, 1080), None),
    ("16:9 (Fit Height)", None, (16, 9)),
    ("9:16 (Fit Height)", None, (9, 16)),
    ("4:3 (Fit Height)", None, (4, 3)),
    ("21:9 (Fit Height)", None, (21, 9)),
]
```

**Key Changes:**
- `RecordingConfig.target_resolution: tuple[int, int] | None` - New field for fixed resolution
- `get_crop_rect()` - Resolution mode scales down proportionally if larger than screen
- `CropOverlayItem.set_resolution()` - Shows fixed-size overlay in preview
- `RecordingSettingsPanel.crop_mode_changed` signal - Emits both resolution and aspect ratio

**Files Modified:**
1. `video_editor/gui/models.py` - Added `target_resolution` field
2. `video_editor/gui/recorder/recording_settings.py` - Added resolution presets
3. `video_editor/gui/recorder/recording_preview.py` - Fixed-size overlay support
4. `video_editor/gui/recorder/recorder_tab.py` - Connected new signals
5. `video_editor/gui/recorder/recording_controller.py` - Added `set_crop_mode()` method

---

### 16. Preview-to-Recording Crop Mismatch Fix

**Problem:** The blue crop overlay in the preview showed a different region than what actually got cropped in the final recording.

**Root Cause:** Coordinate system mismatch:
- Preview overlay used `_container_rect` (video item's display size) for normalization
- FFmpeg used actual screen size for crop calculations
- When these differed (e.g., video item at 1280x720, screen at 2560x1440), offsets mapped to different pixel positions

**Example of the Bug:**
```
Screen: 2560x1440
Video Item: 1280x720 (half size preview)
Crop: 1920x1080

User drags to position → Preview normalizes to container (1280x720 space)
FFmpeg applies offset → Uses screen (2560x1440 space)
Result: Different crop region!
```

**Solution:** Updated `CropOverlayItem` coordinate conversion methods:

1. **`_get_screen_crop_size()`** - New method to calculate crop size in screen pixels
2. **`get_normalized_offset()`** - Now converts container coordinates to screen coordinates before normalizing
3. **`set_normalized_offset()`** - Now converts screen coordinates to container coordinates for display

**Key Insight:** Normalized offsets must always be calculated relative to **screen size**, not the preview's display size. The preview can scale the visual representation, but the offset values must map correctly to screen pixels for FFmpeg.

**Files Modified:**
- `video_editor/gui/recorder/recording_preview.py` - Fixed coordinate conversion in `CropOverlayItem`

---

## Files Modified (January 31, 2026 - Resolution & Crop Fix)

1. **`video_editor/gui/models.py`**
   - Added `target_resolution: tuple[int, int] | None` to `RecordingConfig`
   - Updated `get_crop_rect()` to handle fixed resolutions with proportional scaling
   - Updated `to_ffmpeg_crop_filter()`, `to_dict()`, `from_dict()`, `copy()`

2. **`video_editor/gui/recorder/recording_settings.py`**
   - Replaced `ASPECT_RATIOS` with `CROP_PRESETS`
   - Renamed signal to `crop_mode_changed`
   - Updated handlers for new preset format

3. **`video_editor/gui/recorder/recording_preview.py`**
   - Added `_get_screen_crop_size()` method
   - Fixed `get_normalized_offset()` to use screen coordinates
   - Fixed `set_normalized_offset()` to convert between coordinate systems
   - Added `set_resolution()` to `CropOverlayItem`
   - Added `set_crop_mode()` to `RecordingPreview`

4. **`video_editor/gui/recorder/recorder_tab.py`**
   - Updated to use `crop_mode_changed` signal
   - Added `_on_crop_mode_changed()` handler
   - Initialize screen size on preview

5. **`video_editor/gui/recorder/recording_controller.py`**
   - Added `set_crop_mode()` method
   - Updated `_finalize_recording()` to check both resolution and aspect ratio

---

### 17. Recording Crop Margin Workaround

**Problem:** Despite the coordinate conversion fix, a ~20-30px mismatch persisted between the preview crop overlay and the actual recording crop. The offset appeared consistently to the right and lower.

**Root Cause:** Likely a combination of:
- macOS Retina display scaling (logical points vs physical pixels)
- Menu bar or notch offset differences between Qt's screen geometry and actual capture
- Device pixel ratio not being accounted for in all coordinate conversions

**Solution (Workaround):** Instead of fixing the complex coordinate system mismatch, added a 50px margin around the selected crop area. This captures more than the preview shows, allowing users to fine-tune the exact crop during the editing phase.

**Implementation in `RecordingConfig.get_crop_rect()`:**
```python
def get_crop_rect(self, screen_width: int, screen_height: int, margin: int = 50):
    # ... calculate crop_x, crop_y, crop_width, crop_height ...

    # Apply margin - expand the crop area to capture more than selected
    if margin > 0:
        crop_x = max(0, crop_x - margin)
        crop_y = max(0, crop_y - margin)
        crop_width = min(screen_width - crop_x, crop_width + 2 * margin)
        crop_height = min(screen_height - crop_y, crop_height + 2 * margin)

    return (crop_x, crop_y, crop_width, crop_height)
```

**Result:**
- Selecting 1920x1080 now captures ~2020x1180 (with margins clamped to screen bounds)
- User can precisely crop to exact dimensions during editing
- No more content cutoff due to coordinate mismatch

**Files Modified:**
- `video_editor/gui/models.py` - Added `margin` parameter to `get_crop_rect()`
