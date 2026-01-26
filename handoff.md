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

## Known Trade-offs

1. **Re-encoding vs Speed**: Frame-accurate cuts require re-encoding, which is slower than stream copy but necessary for caption synchronization.

2. **Soniox Granularity**: More segments means better precision but requires robust retake detection algorithms.

3. **LLM Take Selection**: Uses Gemini 2.0 Flash (primary) or OpenAI (fallback) for selecting best takes. Falls back to duration-based selection if no API keys available.
