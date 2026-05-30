import os
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSizeF
from PySide6.QtWidgets import QApplication

from video_editor.gui.models import RecordingConfig
from video_editor.gui.recorder.ffmpeg_worker import FFmpegCropWorker
from video_editor.gui.recorder.recorder_tab import RecorderTab
from video_editor.runtime_paths import ffmpeg_executable, ffprobe_executable


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_record_button_uses_current_preview_crop_offset(qt_app):
    tab = RecorderTab()
    try:
        tab._preview._on_video_size_changed(QSizeF(3440, 1440))
        tab._preview.set_screen_size(3440, 1440)
        tab.set_config(
            RecordingConfig(
                capture_full_screen=False,
                target_resolution=(1920, 1080),
                crop_offset_x=0.5,
                crop_offset_y=0.5,
            )
        )
        tab._preview.set_crop_offset(1.0, 0.0)

        captured_configs: list[RecordingConfig] = []

        def fake_start_recording() -> bool:
            captured_configs.append(tab._controller.config.copy())
            return True

        tab._controller.start_recording = fake_start_recording

        tab._on_record_clicked()

        assert len(captured_configs) == 1
        config = captured_configs[0]
        assert config.crop_offset_x == pytest.approx(1.0)
        assert config.crop_offset_y == pytest.approx(0.0)
        assert config.to_ffmpeg_crop_filter(3440, 1440, margin=0) == "crop=1920:1080:1520:0"
    finally:
        tab.close()


def test_ffmpeg_post_crop_uses_selected_offset_pixels(tmp_path: Path):
    ffmpeg = ffmpeg_executable()
    ffprobe = ffprobe_executable()
    raw_path = tmp_path / "raw.mp4"
    output_path = tmp_path / "cropped.mp4"

    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            (
                "color=c=red:size=152x144:rate=10:duration=1[left];"
                "color=c=blue:size=192x144:rate=10:duration=1[right];"
                "[left][right]hstack=inputs=2,format=yuv420p"
            ),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            str(raw_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    config = RecordingConfig(
        capture_full_screen=False,
        target_resolution=(192, 108),
        crop_offset_x=1.0,
        crop_offset_y=0.0,
    )
    crop_filter = config.to_ffmpeg_crop_filter(344, 144, margin=0)
    assert crop_filter == "crop=192:108:152:0"

    worker = FFmpegCropWorker(
        input_path=raw_path,
        output_path=output_path,
        crop_filter=crop_filter,
        encoder_args=["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"],
    )
    success, result_path, message = worker.run()

    assert success, message
    assert result_path == output_path

    dimensions = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert dimensions == "192x108"

    pixel = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(output_path),
            "-frames:v",
            "1",
            "-vf",
            "crop=2:2:2:10,format=rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    ).stdout
    red, green, blue = pixel[:3]
    assert blue > 150
    assert red < 80
    assert green < 80
