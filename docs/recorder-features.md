# Recorder features

## Audio-only recording

Enable **Record audio only (high quality)** in the Recorder tab to save the
selected microphone as an `.m4a` file without capturing the display. Audio-only
files are saved directly in the configured recordings folder instead of its
`raw` subfolder.

The audio-only recorder deliberately uses Qt's `QMediaRecorder` with the
selected `QAudioInput`. On macOS this uses the native AVFoundation capture
pipeline. The requested format is AAC in an M4A container, 48 kHz, stereo, at
256 kb/s.

The operating system and selected device ultimately choose the supported AAC
profile and bitrate. Treat the displayed settings as the requested format; use
`ffprobe` on a completed recording when the exact encoded format matters:

```bash
ffprobe -v error -select_streams a:0 -show_entries \
  stream=codec_name,sample_rate,channels,bit_rate -of default=nw=1 recording.m4a
```

Audio-only takes can also be opened in the Editor and exported after removing
unwanted ranges. The Export action automatically offers an `.m4a` output,
concatenates only the kept audio ranges, and skips video-only crop and caption
processing.

### Reliability rules

- The input-level meter is stopped before recording so it does not compete for
  the microphone.
- Screen capture is detached while recording audio only and reattached when
  the take finishes, so no display permission is required for this mode.
- Video Editor refuses to close while a recording is active. If a previous
  legacy FFmpeg recorder was left running after a crash, the next launch stops
  only that app-owned orphan process and lets it finalize its file.
- Do not route audio-only recording through direct FFmpeg `avfoundation`
  microphone capture. That path was tested for bitrate control but produced
  occasional dropped fragments of spoken words in real recordings.
- Direct FFmpeg recording remains available for the separate screen-crop
  workflow; it is not the audio-only backend.

After a backend change, make one continuous spoken test recording and listen
for complete words before relying on it for a longer take. Automated tests can
verify the recorder lifecycle, but cannot reproduce a live microphone or
device-driver dropout.

## Teleprompter

The audio-only screen can show a teleprompter to make voice recording easier:

1. Enter or paste a script in the teleprompter field.
2. Select a readable text size: **Compact**, **Small**, **Medium**, or
   **Large**.
3. Set **Reading speed** in words per minute. The control supports 40--240 WPM.
4. Start the teleprompter before or during a recording; pause or restart it as
   needed.

Scrolling is rendered smoothly in pixel increments. The rate is calculated
from the document's rendered height and the selected WPM, rather than jumping
one text line at a time.

## Packaging and verification

For a Finder-launchable macOS build that does not include local credential
configuration, run:

```bash
venv/bin/python packaging/macos/build_app.py --no-bundle-env --no-dmg
```

Before replacing an installed app, quit **Video Editor**. Verify the built app
and then copy it to `/Applications`:

```bash
codesign --verify --deep --strict "dist/macos/Video Editor.app"
```

The focused regression suite for this area is:

```bash
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest -q \
  test/test_audio_only_recording.py \
  test/test_teleprompter.py \
  test/test_recording_config.py \
  test/test_recorder_failure_handling.py
```
