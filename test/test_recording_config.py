from pathlib import Path

from video_editor.gui.models import RecordingConfig
from video_editor.gui.recorder.recorder_tab import (
    cropped_recording_output_path,
    select_recording_crop_config,
    should_post_process_crop,
)


def test_recording_config_only_needs_crop_when_crop_mode_selected():
    config = RecordingConfig()
    assert config.needs_crop_output is False
    assert config.to_ffmpeg_crop_filter(3440, 1440, margin=0) is None

    config.capture_full_screen = False
    config.target_resolution = (1920, 1080)

    assert config.needs_crop_output is True


def test_recording_config_system_audio_defaults_off():
    assert RecordingConfig().system_audio_enabled is False
    assert RecordingConfig.from_dict({}).system_audio_enabled is False


def test_audio_only_config_round_trips_and_never_requests_crop_output():
    config = RecordingConfig(
        audio_only=True,
        capture_full_screen=False,
        target_resolution=(1920, 1080),
    )

    restored = RecordingConfig.from_dict(config.to_dict())

    assert restored.audio_only is True
    assert restored.copy().audio_only is True
    assert restored.needs_crop_output is False
    assert restored.to_ffmpeg_crop_filter(3440, 1440, margin=0) is None


def test_recording_config_exact_preview_crop_filter_uses_no_margin():
    config = RecordingConfig(
        capture_full_screen=False,
        target_resolution=(1920, 1080),
        crop_offset_x=0.5,
        crop_offset_y=0.5,
    )

    assert config.to_ffmpeg_crop_filter(3440, 1440, margin=0) == "crop=1920:1080:760:180"


def test_recording_config_preview_offset_changes_crop_filter_position():
    config = RecordingConfig(
        capture_full_screen=False,
        target_resolution=(1920, 1080),
        crop_offset_x=1.0,
        crop_offset_y=0.0,
    )

    assert config.to_ffmpeg_crop_filter(3440, 1440, margin=0) == "crop=1920:1080:1520:0"


def test_recording_config_crop_filter_can_keep_legacy_margin():
    config = RecordingConfig(
        capture_full_screen=False,
        target_resolution=(1920, 1080),
        crop_offset_x=0.5,
        crop_offset_y=0.5,
    )

    assert config.to_ffmpeg_crop_filter(3440, 1440) == "crop=2020:1180:710:130"


def test_cropped_recording_output_path_places_raw_capture_next_to_raw_directory():
    raw_path = Path("/Users/example/Movies/Recordings/raw/recording_20260509_182444.mp4")

    assert cropped_recording_output_path(raw_path) == Path(
        "/Users/example/Movies/Recordings/recording_20260509_182444.mp4"
    )


def test_cropped_recording_output_path_uses_suffix_when_input_is_not_in_raw_dir():
    input_path = Path("/tmp/recording_20260509_182444.mp4")

    assert cropped_recording_output_path(input_path) == Path(
        "/tmp/recording_20260509_182444_cropped.mp4"
    )


def test_crop_decision_uses_ui_config_when_backend_flag_is_wrong_for_raw_backup():
    ui_config = RecordingConfig(
        capture_full_screen=False,
        target_resolution=(1920, 1080),
    )
    raw_path = Path("/Users/example/Movies/Recordings/raw/recording_20260509_184146.mp4")

    config = select_recording_crop_config(None, ui_config)

    assert config == ui_config
    assert should_post_process_crop(raw_path, backend_needs_crop=False, config=config) is True


def test_crop_decision_does_not_recrop_non_raw_legacy_output():
    ui_config = RecordingConfig(
        capture_full_screen=False,
        target_resolution=(1920, 1080),
    )
    direct_output_path = Path("/Users/example/Movies/Recordings/recording_20260509_184146.mp4")

    assert should_post_process_crop(
        direct_output_path,
        backend_needs_crop=False,
        config=ui_config,
    ) is False
