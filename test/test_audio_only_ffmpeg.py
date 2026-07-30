from pathlib import Path

from video_editor.gui.recorder.ffmpeg_recorder import FFmpegRecorder


def test_audio_only_command_enforces_high_quality_aac(monkeypatch):
    monkeypatch.setattr(
        FFmpegRecorder,
        "_get_best_aac_encoder",
        staticmethod(lambda: "aac_at"),
    )

    command = FFmpegRecorder._build_audio_recording_command(
        audio_device_index=1,
        output_path=Path("recording.m4a"),
        audio_sample_rate=48000,
        audio_channels=2,
        audio_bitrate=256000,
    )

    assert command[command.index("-i") + 1] == ":1"
    assert command[command.index("-c:a") + 1] == "aac_at"
    assert command[command.index("-b:a") + 1] == "256000"
    assert command[command.index("-ar") + 1] == "48000"
    assert command[command.index("-ac") + 1] == "2"
