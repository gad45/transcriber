from video_editor.gui.models import RecordingConfig


def test_recording_config_only_needs_crop_when_crop_mode_selected():
    config = RecordingConfig()
    assert config.needs_crop_output is False
    assert config.to_ffmpeg_crop_filter(3440, 1440, margin=0) is None

    config.capture_full_screen = False
    config.target_resolution = (1920, 1080)

    assert config.needs_crop_output is True


def test_recording_config_exact_preview_crop_filter_uses_no_margin():
    config = RecordingConfig(
        capture_full_screen=False,
        target_resolution=(1920, 1080),
        crop_offset_x=0.5,
        crop_offset_y=0.5,
    )

    assert config.to_ffmpeg_crop_filter(3440, 1440, margin=0) == "crop=1920:1080:760:180"


def test_recording_config_crop_filter_can_keep_legacy_margin():
    config = RecordingConfig(
        capture_full_screen=False,
        target_resolution=(1920, 1080),
        crop_offset_x=0.5,
        crop_offset_y=0.5,
    )

    assert config.to_ffmpeg_crop_filter(3440, 1440) == "crop=2020:1180:710:130"
