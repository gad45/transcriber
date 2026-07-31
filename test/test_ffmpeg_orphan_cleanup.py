from pathlib import Path

from video_editor.gui.recorder.ffmpeg_recorder import FFmpegRecorder


def test_finds_only_app_owned_orphaned_recorder_processes(monkeypatch, tmp_path: Path):
    app_ffmpeg = tmp_path / "Video Editor.app" / "Contents" / "Frameworks" / "bin" / "ffmpeg"
    app_ffmpeg.parent.mkdir(parents=True)
    app_ffmpeg.touch()
    recordings_dir = Path.home() / "Movies" / "Recordings"
    legacy_output = recordings_dir / "legacy.m4a"
    marked_output = tmp_path / "screen.mp4"
    monkeypatch.setattr("video_editor.gui.recorder.ffmpeg_recorder.FFMPEG", str(app_ffmpeg))

    matches = FFmpegRecorder._find_orphaned_recordings(
        "\n".join(
            [
                f"101 1 {app_ffmpeg} -y -f avfoundation -i :1 {legacy_output}",
                (
                    f"102 1 {app_ffmpeg} -y -f avfoundation -i 0:1 "
                    f"-metadata comment=video-editor-recorder {marked_output}"
                ),
                f"103 999 {app_ffmpeg} -y -f avfoundation -i :1 {legacy_output}",
                f"104 1 /usr/local/bin/ffmpeg -y -f avfoundation -i :1 {legacy_output}",
            ]
        )
    )

    assert matches == [(101, legacy_output), (102, marked_output)]
