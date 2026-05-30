# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-powered video editor for Hungarian spoken content. Transcribes video via the Soniox API, uses Gemini to pick the best take across retakes, removes silences, and burns streaming captions. Ships as both a PySide6 GUI (with a built-in screen recorder) and two CLIs: a one-shot pipeline (`video-editor`) and a headless Codex workflow (`video-editor-codex`) that produces editable `.vedproj` files plus QC reports.

## Common Commands

```bash
# Install (editable)
source venv/bin/activate
pip install -e .[dev]

# CLI: one-shot transcribe → cut → caption pipeline
python -m video_editor input.mp4 -o output.mp4
python -m video_editor input.mp4 --preview                      # propose cuts only
python -m video_editor input.mp4 --silence-threshold 1.5 --caption-style modern

# Headless Codex workflow (produces editable .vedproj + analysis report)
video-editor-codex analyze recording.mp4 --project recording.vedproj
video-editor-codex export  recording.vedproj -o cut.mp4
video-editor-codex qc      recording.vedproj --export cut.mp4
video-editor-codex finish  recording.vedproj --write-back        # full analyze→merge→export→QC loop

# GUI (optionally with a video to load)
python -m video_editor.gui_main [path/to/video.mp4]

# Tests
pytest                                  # all tests
pytest test/test_recorder_failure_handling.py::test_native_recorder_finished_after_stop_is_success
```

## Environment

`.env` in repo root (or GUI Settings → keys saved to `~/.video_editor_settings`):

```bash
SONIOX_API_KEY=...      # required for transcription
GEMINI_API_KEY=...      # required for take selection + transcription QC (falls back to duration heuristic)
OPENAI_API_KEY=...      # optional Gemini fallback for take selection
```

The CLI calls `environment.load_app_env()` on startup, which checks (in order): a bundled `.env` next to a frozen executable, repo root, CWD, the `.app/..` parent for macOS bundles, and `VIDEO_EDITOR_ENV_PATH`. `runtime_paths.ffmpeg_executable()` resolves `ffmpeg`/`ffprobe` similarly — bundled binaries first, then `PATH`, with `VIDEO_EDITOR_FFMPEG_PATH` / `VIDEO_EDITOR_FFPROBE_PATH` overrides. **Never call `ffmpeg`/`ffprobe` directly** — always go through `runtime_paths` so the macOS `.app` bundle works.

## Architecture

### Two parallel pipelines

The CLI (`main.py`) runs a one-shot pipeline that writes a final MP4 directly. The Codex CLI (`codex_cli.py`) plus the GUI both run a **project-centric** pipeline: analysis writes a `.vedproj` (an editable JSON document), and a separate export step renders MP4s from that project. The two pipelines share the underlying modules (`transcriber`, `analyzer`, `cutter`, `captioner`, `qc`) but the project flow is what most modern features build on.

```
                           Codex CLI / GUI
                          ┌──────────────────────────┐
analysis_pipeline.py  ──▶ │  recording.vedproj  ◀───┐│
   transcriber              (segments, tokens,        │
   analyzer (LLM)            user keep/cut, crop,    │ Headless or GUI
   visual_analysis           highlights, captions)   │ edits feed back
   qc (transcription QC)                             │ into the same file
                                                     │
export_pipeline.py    ◀──────────────────────────────┘
   cutter (stream copy)
   captioner (libx264 burn)
                          ──▶ output.mp4
qc_pipeline.py
   re-transcribes export, diffs vs. project transcript,
   audits cut boundaries, flags silent screen activity
                          ──▶ qc.json / qc.md / review clips
```

### Core modules (`video_editor/`)

- `transcriber.py` — extracts MP3 via FFmpeg, uploads to Soniox, returns `Segment` and word-level `Token` objects.
- `analyzer.py` — silence detection, retake clustering with `rapidfuzz`, LLM take selection (Gemini 2.0 Flash via `google-genai`, fallback to longest-take heuristic if no key). Owns `SegmentAction` (KEEP/REMOVE), `TimeRange`, `RetakeGroup`, `HUNGARIAN_HESITATION_MARKERS`.
- `cutter.py` — FFmpeg stream-copy segment extraction + concat. `Cutter.SEGMENT_GAP = 0.2` is the freeze-frame pad inserted between cuts. `cut_video()` accepts an optional `crop_filter` (forces re-encode when set).
- `captioner.py` — SRT generation, streaming word-by-word caption burning via libx264, soft-subtitle muxing.
- `qc.py` — Gemini-powered transcription quality control: validates Hungarian segments, optionally auto-corrects text. Distinct from `qc_pipeline.py` (export QC).
- `visual_analysis.py` — frame-hash motion scoring inside no-speech gaps, used to flag silent screencast sections that should be force-included.
- `encoder.py` — `get_encoder_args()` returns `h264_videotoolbox` on macOS when available, falling back to `libx264 -crf 18 -preset medium`. Detection is cached.
- `runtime_paths.py` / `environment.py` — bundled-resource resolution for PyInstaller `.app` bundles. Always used to locate `ffmpeg`/`ffprobe` and `.env`.
- `project_io.py` — the `.vedproj` schema. `ProjectData` dataclass + `load_project`, `write_project`, `build_project_payload`, plus mutation helpers (`add_highlight_range`, `set_project_crop`, `merge_project_with_analysis`, `repair_project_boundaries`, `backup_project_file`). `get_final_keep_ranges` and `get_final_tokens` are the canonical "what does the export contain" accessors.

### Headless pipeline modules

- `analysis_pipeline.py` — `analyze_recording()` runs transcribe → analyze → optional Gemini QC → silent-visual detection. Returns an `AnalysisResult` with `SegmentDecision`, `CutRange`, `QuestionableRange`, retake reports, plus serializers `write_analysis_json`, `write_analysis_markdown`, `write_analysis_project`.
- `export_pipeline.py` — `export_project()` renders an MP4 from a `ProjectData`. Resolves the source video, applies the project's `crop_config` (or infers one from a `.crop.json` sidecar), cuts via `Cutter`, optionally burns captions adjusted onto the cut timeline by `adjust_tokens_for_cuts()`. `crop_filter_from_project()` is the single source of truth for project→FFmpeg crop conversion.
- `qc_pipeline.py` — `run_quality_control()` independently transcribes the exported MP4, fuzzy-diffs against the project's expected transcript (`SEGMENT_MATCH_THRESHOLD`/`EDGE_PHRASE_THRESHOLD`/`REMOVED_TEXT_THRESHOLD`), checks `BOUNDARY_START_MARGIN`/`BOUNDARY_END_MARGIN` around every cut, and audits removed retakes for unique content. Returns `QCResult` with status `pass`/`needs_review`/`needs_fix`. Also: `repair_project_boundaries()` (snaps cuts off mid-token), `write_qc_review_clips()`, `write_silent_visual_contact_sheets()`.
- `codex_workflow.py` — `finish_edit()` orchestrates analyze → merge with manual project → repair boundaries → export → QC, optionally writing back to the input `.vedproj` (with timestamped backup) and packaging upload-ready artifacts.
- `codex_cli.py` — Click group exposing the workflow. Subcommands: `analyze`, `export`, `qc`, `finish`, `include-range`, `set-crop` (`--copy-from`/`--rect`/`--clear`), `apply-analysis` (merge manual + analyzed), `review-silence`, `repair-boundaries`.

### `.vedproj` data model

A `.vedproj` is a JSON document containing the **original recording path** (never the cut output), the full transcript (`segments` + word-level `tokens`), per-segment user overrides (`segment_overrides`, `segment_text_overrides`), force-include `highlights`, a `crop_config` (normalized 0–1 fractions and pan), `caption_settings`, plus optional `metadata` (analysis report paths, strict-mode flag, etc.). This means edits in the GUI and headless edits via `codex_cli` operate on the same file and round-trip safely. `get_final_keep_ranges()` is what every export/QC path uses to compute what's actually in the cut.

### GUI (`video_editor/gui/`)

PySide6 (Qt 6) with two tabs:

**Editor** — `main_window.py` orchestrates `video_player.py` (QMediaPlayer + crop/caption overlay), `timeline.py` (QGraphicsView, segment + highlight items in `segment_item.py`), `transcript_editor.py`, `caption_settings.py`, and `settings_dialog.py`. State lives in `gui/models.py:EditSession` (segments, tokens, user edits, highlights, `CropConfig`, `CaptionSettings`) — this mirrors the `.vedproj` schema and is what serializes via `to_dict`/`from_dict`.

**Recorder** — `recorder/recorder_tab.py` drives `RecordingController` (`recording_controller.py`), which dispatches to one of three recording backends:

1. **Qt `QMediaRecorder` + `QScreenCapture`** (default) — cross-platform, full-screen capture; cropping is post-processed via `FFmpegCropWorker` (`ffmpeg_worker.py`).
2. **`FFmpegRecorder`** (`ffmpeg_recorder.py`) — direct FFmpeg `avfoundation`/`gdigrab`/`x11grab` capture with built-in crop. Used when the user opts into direct-crop recording.
3. **`NativeMacOSRecorder`** (`macos_native_recorder.py`) — spawns a Swift helper (`macos_system_audio_helper.swift`, compiled on first use, cached at `~/Library/Caches/video_editor/`) that uses `ScreenCaptureKit` for macOS 15+ system-audio capture. The Python side talks to the helper over JSON-line stdout/stderr.

The controller exposes a unified Qt signal interface (`recording_started`, `recording_stopped(path, needs_ffmpeg_crop)`, `recording_error`, `audio_level_changed`, etc.) regardless of which backend is active. `recorder_tab.py` post-processes the recording (cropping, raw-backup placement) based on the `needs_ffmpeg_crop` flag and `RecordingConfig.needs_crop_output`.

### LLM providers

- Take selection: Gemini 2.0 Flash (`google-genai`). Falls back to OpenAI (`openai>=1.0`) if `OPENAI_API_KEY` is set, then to a duration-based heuristic.
- Transcription QC (Hungarian grammar/coherence): Gemini, batched via `QualityController.BATCH_SIZE`.
- Export QC: independent re-transcription via Soniox, no LLM (deterministic rapidfuzz comparison).

## Key signal flows (GUI editor)

```
TimelineView.mousePressEvent      → seek_requested        → MainWindow._on_seek_requested → VideoPlayer.seek_seconds
TimelineView drag (empty space)   → highlight_created     → MainWindow._on_highlight_created → EditSession.add_highlight → Timeline.add_highlight
TranscriptEditor checkbox toggle  → keep_changed          → MainWindow._on_segment_keep_changed → EditSession.set_segment_kept → Timeline.update_segment
CaptionSettingsPanel change       → settings_changed      → MainWindow._on_caption_settings_changed → VideoPlayer.update_caption_settings
RecordingController state         → recording_stopped     → RecorderTab._on_recording_stopped → optional FFmpegCropWorker
```

## FFmpeg conventions

- Audio extraction → MP3 for Soniox upload.
- Cutting → stream copy (`-c copy`) when no crop, otherwise `encoder.get_encoder_args()` (VideoToolbox if available, else libx264).
- Caption burning → always re-encodes (libx264 in the burn path).
- `Cutter.SEGMENT_GAP = 0.2` — gap inserted between concatenated segments via a freeze-frame `tpad` filter to avoid audio glitches and keep cuts visually intelligible.
- Recording on macOS: `avfoundation` for the FFmpeg backend, `ScreenCaptureKit` (Swift helper) for the native backend.

## Recipes for common changes

**Add a timeline feature**
1. Add a field to `gui/models.py:EditSession` with `to_dict`/`from_dict` round-tripping — and mirror it in `project_io.py` so `.vedproj` files written by the GUI and Codex CLI stay compatible.
2. Add a `QGraphicsItem` in `gui/segment_item.py`.
3. Wire signals in `gui/timeline.py`, connect handlers in `gui/main_window.py`.

**Change export behavior**
- GUI export: `gui/main_window.py:_export_video()`.
- Headless export: `export_pipeline.py:export_project()`.
- Both consume `project_io.get_final_keep_ranges()` and `get_final_tokens()`. Token timing is mapped onto the cut timeline by `export_pipeline.adjust_tokens_for_cuts()` (and the GUI's `_adjust_tokens_for_cuts` mirror — keep them aligned).

**Add a `video-editor-codex` subcommand**
1. Add a function to `analysis_pipeline.py` / `qc_pipeline.py` / `codex_workflow.py` (whichever owns the operation).
2. Add `@main.command(...)` in `codex_cli.py`. Reuse `_load_runtime_env()`, `_build_config()`, and the default-path helpers (`_default_project_path`, etc.).
3. Mutating commands should accept an `--output` that defaults to in-place rewriting and use `project_io.write_project()` (atomic via temp file + rename).

**Add a recording feature**
1. New field on `RecordingConfig` (`gui/models.py`).
2. UI in `gui/recorder/recording_settings.py:RecordingSettingsPanel`.
3. Plumb into `RecordingController._apply_config()`. If FFmpeg-side, also update `FFmpegRecorder._build_command()`. If macOS-native, update both the Python side in `macos_native_recorder.py` and the Swift helper in `macos_system_audio_helper.swift` (and bump the cached helper hash so it gets recompiled).

**Add a keyboard shortcut**: `gui/main_window.py:_setup_shortcuts()`, `QShortcut(QKeySequence(...), self)`, connect handler.

## macOS app packaging

```bash
python packaging/macos/build_app.py            # auto-detects + bundles ffmpeg/ffprobe and .env
python packaging/macos/build_app.py --no-bundle-ffmpeg --no-bundle-env
python packaging/macos/build_app.py --ffmpeg-bin /path --ffprobe-bin /path
```

Produces `dist/macos/Video Editor.app` and `dist/macos/VideoEditor-<version>.dmg`. The packaged app requests macOS privacy permissions (Screen Recording, Microphone) under "Video Editor" instead of "Terminal" (which is what `launch_gui.command` ends up registered as).

## Testing notes

- `pytest` runs all tests in `test/`. The recorder tests instantiate `QCoreApplication` (no GUI) — see `test_recorder_failure_handling.py`.
- Test fixtures live in `test/` alongside tests (`test.mp4`, sample `.vedproj` files like `1.vedproj`, `2.vedproj`).
- For manual GUI smoke tests: `python -m video_editor.gui_main test/test.mp4`.
- For end-to-end Codex workflow validation against a sample: `video-editor-codex analyze test/test.mp4 --project /tmp/x.vedproj && video-editor-codex export /tmp/x.vedproj -o /tmp/x.mp4 && video-editor-codex qc /tmp/x.vedproj --export /tmp/x.mp4`.

## Troubleshooting

- **API key errors**: GUI Settings menu, or `.env` in repo root. The Codex CLI also reads `~/.video_editor_settings` (the GUI's local store).
- **`ffmpeg` not found in `.app` bundle**: ensure `build_app.py` was run without `--no-bundle-ffmpeg`, or set `VIDEO_EDITOR_FFMPEG_PATH`.
- **Export has no captions**: tokens must exist on the project (`segments` carry the text but tokens carry word-level timing); verify `caption_settings.enabled` is true and `get_final_tokens()` returns a non-empty list.
- **Cut crosses a word boundary**: run `video-editor-codex repair-boundaries project.vedproj` (or `finish` does it automatically up to `--max-fix-passes`).
- **Recording crop mismatch with preview**: the FFmpeg post-processing crop applies a 50px margin to compensate for coordinate-system differences between Qt's preview and the actual capture rect. This is intentional.
- **macOS system-audio capture missing**: requires macOS 15+ and the `NativeMacOSRecorder` backend. The Swift helper compiles on first launch — check `~/Library/Caches/video_editor/` for compile errors.
