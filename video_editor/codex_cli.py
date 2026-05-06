"""Codex-facing CLI for recording analysis and editable project creation."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from .analysis_pipeline import (
    analyze_recording,
    write_analysis_json,
    write_analysis_markdown,
    write_analysis_project,
)
from .config import Config
from .environment import load_app_env
from .export_pipeline import export_project
from .project_io import add_highlight_range, format_time, load_project, parse_time

console = Console()


def _load_runtime_env() -> None:
    """Load repo/bundled env and the GUI's local settings file if present."""
    load_app_env()

    # Match the existing GUI settings behavior without printing secret values.
    settings_path = Path.home() / ".video_editor_settings"
    if not settings_path.exists():
        return

    try:
        for line in settings_path.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key in {"SONIOX_API_KEY", "GEMINI_API_KEY"} and value:
                os.environ.setdefault(key, value)
    except OSError:
        return


def _default_project_path(input_video: Path) -> Path:
    return input_video.with_suffix(".vedproj")


def _default_report_path(input_video: Path) -> Path:
    return input_video.with_suffix(".analysis.json")


def _default_markdown_path(input_video: Path) -> Path:
    return input_video.with_suffix(".analysis.md")


def _build_config(openai_key: str | None, keep_temp: bool) -> Config:
    return Config(
        openai_api_key=openai_key,
        keep_temp=keep_temp,
        temp_dir=Path(tempfile.gettempdir()) / "video_editor",
    )


@click.group()
def main() -> None:
    """Analyze recordings, save editable projects, and export AI cuts."""
    _load_runtime_env()


@main.command()
@click.argument("input_video", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--project", "project_path", type=click.Path(dir_okay=False, path_type=Path),
              help="Editable .vedproj output path. Defaults beside the video.")
@click.option("--report", "report_path", type=click.Path(dir_okay=False, path_type=Path),
              help="Machine-readable JSON report path. Defaults beside the video.")
@click.option("--markdown", "markdown_path", type=click.Path(dir_okay=False, path_type=Path),
              help="Human-readable Markdown report path. Defaults beside the video.")
@click.option("--strict/--conservative", default=True, show_default=True,
              help="Strict mode uses wider word-boundary padding and stronger review flags.")
@click.option("--skip-qc", is_flag=True, help="Skip Gemini transcription quality control.")
@click.option("--qc-report-only", is_flag=True,
              help="Run QC but do not apply automatic text corrections.")
@click.option("--suggest-silent-visual-ranges/--no-suggest-silent-visual-ranges",
              default=True, show_default=True,
              help="Check no-speech gaps for screen activity and report review ranges.")
@click.option("--silent-visual-min-gap", type=float, default=2.0, show_default=True,
              help="Minimum no-speech gap to check for visual activity.")
@click.option("--visual-motion-threshold", type=float, default=0.2, show_default=True,
              help="Frame-change score required to flag a silent visual range.")
@click.option("--auto-include-visual-ranges", is_flag=True,
              help="Save suggested silent visual ranges as force-include highlights in the project.")
@click.option("--export-output", type=click.Path(dir_okay=False, path_type=Path),
              help="Optional AI-cut video to export after saving the project.")
@click.option("--no-captions", is_flag=True, help="When exporting, skip burned captions.")
@click.option("--openai-key", envvar="OPENAI_API_KEY",
              help="OpenAI API key fallback for take selection.")
@click.option("--keep-temp", is_flag=True, help="Keep temporary processing files.")
def analyze(
    input_video: Path,
    project_path: Path | None,
    report_path: Path | None,
    markdown_path: Path | None,
    strict: bool,
    skip_qc: bool,
    qc_report_only: bool,
    suggest_silent_visual_ranges: bool,
    silent_visual_min_gap: float,
    visual_motion_threshold: float,
    auto_include_visual_ranges: bool,
    export_output: Path | None,
    no_captions: bool,
    openai_key: str | None,
    keep_temp: bool,
) -> None:
    """Transcribe a recording, identify bad takes, and save an editable project."""
    input_video = input_video.expanduser().resolve()
    project_path = project_path or _default_project_path(input_video)
    report_path = report_path or _default_report_path(input_video)
    markdown_path = markdown_path or _default_markdown_path(input_video)

    config = _build_config(openai_key, keep_temp)

    result = analyze_recording(
        input_video,
        config=config,
        strict=strict,
        skip_qc=skip_qc,
        qc_report_only=qc_report_only,
        suggest_silent_visual_ranges=suggest_silent_visual_ranges,
        silent_visual_min_gap=silent_visual_min_gap,
        visual_motion_threshold=visual_motion_threshold,
    )

    report_path = write_analysis_json(result, report_path)
    markdown_path = write_analysis_markdown(result, markdown_path)
    project_path = write_analysis_project(
        result,
        project_path,
        metadata={
            "json_report": str(report_path),
            "markdown_report": str(markdown_path),
            "strict": strict,
        },
        auto_include_visual_ranges=auto_include_visual_ranges,
    )

    exported_path = None
    if export_output:
        project = load_project(project_path)
        exported_path = export_project(
            project,
            export_output,
            config=config,
            no_captions=no_captions,
        )

    console.print(Panel.fit(
        "\n".join([
            "[bold green]Analysis complete[/bold green]",
            f"Project: [cyan]{project_path}[/cyan]",
            f"JSON report: [cyan]{report_path}[/cyan]",
            f"Markdown report: [cyan]{markdown_path}[/cyan]",
            f"Kept duration estimate: {format_time(result.kept_duration)}",
            f"Removed duration estimate: {format_time(result.removed_duration)}",
            f"Questionable ranges: {len(result.questionable_ranges)}",
            *([f"Exported video: [cyan]{exported_path}[/cyan]"] if exported_path else []),
        ]),
        border_style="green",
    ))


@main.command("include-range")
@click.argument("project", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--start", "start_value", required=True,
              help="Range start as seconds, MM:SS.mmm, or HH:MM:SS.mmm.")
@click.option("--end", "end_value", required=True,
              help="Range end as seconds, MM:SS.mmm, or HH:MM:SS.mmm.")
@click.option("--label", default="", help="Optional label for the force-include range.")
def include_range(project: Path, start_value: str, end_value: str, label: str) -> None:
    """Append a force-include highlight to an editable project."""
    start = parse_time(start_value)
    end = parse_time(end_value)
    highlight = add_highlight_range(project, start=start, end=end, label=label)
    console.print(
        f"[green]Added highlight[/green] "
        f"{format_time(highlight['start'])}-{format_time(highlight['end'])} "
        f"to [cyan]{Path(project).expanduser().resolve()}[/cyan]"
    )


@main.command()
@click.argument("project", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", "output_path", required=True,
              type=click.Path(dir_okay=False, path_type=Path),
              help="Output video path.")
@click.option("--no-captions", is_flag=True, help="Skip burned captions.")
@click.option("--keep-temp", is_flag=True, help="Keep temporary processing files.")
def export(project: Path, output_path: Path, no_captions: bool, keep_temp: bool) -> None:
    """Export a saved project from the original recording source."""
    loaded = load_project(project)
    output = export_project(
        loaded,
        output_path,
        config=_build_config(openai_key=None, keep_temp=keep_temp),
        no_captions=no_captions,
    )
    console.print(f"[green]Exported[/green] [cyan]{output}[/cyan]")


if __name__ == "__main__":
    main()

