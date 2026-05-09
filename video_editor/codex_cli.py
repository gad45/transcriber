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
from .codex_workflow import finish_edit
from .config import Config
from .cutter import Cutter
from .environment import load_app_env
from .export_pipeline import export_project
from .project_io import (
    add_highlight_range,
    crop_config_from_rect,
    format_time,
    load_project,
    merge_project_with_analysis,
    parse_time,
    set_project_crop,
)
from .qc_pipeline import (
    issue_summary,
    repair_project_boundaries,
    run_quality_control,
    write_silent_visual_contact_sheets,
    write_silent_visual_review_files,
    write_qc_json,
    write_qc_markdown,
    write_qc_review_clips,
)

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


def _default_qc_report_path(project_path: Path) -> Path:
    return project_path.with_suffix(".qc.json")


def _default_qc_markdown_path(project_path: Path) -> Path:
    return project_path.with_suffix(".qc.md")


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


@main.command("set-crop")
@click.argument("project", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--copy-from", "copy_from", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Copy crop_config from another .vedproj file.")
@click.option("--rect", nargs=4, type=int, metavar="X Y WIDTH HEIGHT",
              help="Set crop from source-video pixel rectangle.")
@click.option("--clear", is_flag=True, help="Clear the global crop.")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path),
              help="Project output path. Defaults to updating the project in place.")
def set_crop(
    project: Path,
    copy_from: Path | None,
    rect: tuple[int, int, int, int] | None,
    clear: bool,
    output_path: Path | None,
) -> None:
    """Set a project's global crop for headless browser-window exports."""
    selected_modes = sum(1 for value in (copy_from, rect, clear) if value)
    if selected_modes != 1:
        raise click.UsageError("Choose exactly one of --copy-from, --rect, or --clear.")

    crop_config = None
    detail = "cleared"
    if copy_from:
        reference = load_project(copy_from)
        crop_config = reference.raw.get("crop_config")
        if not crop_config:
            raise click.ClickException(f"No crop_config found in {copy_from}")
        detail = f"copied crop from {Path(copy_from).expanduser().resolve()}"
    elif rect:
        loaded = load_project(project)
        video_width, video_height = Cutter(_build_config(openai_key=None, keep_temp=False)).get_video_dimensions(
            loaded.video_path,
        )
        x, y, width, height = rect
        crop_config = crop_config_from_rect(
            x=x,
            y=y,
            width=width,
            height=height,
            video_width=video_width,
            video_height=video_height,
        )
        detail = f"set crop rectangle {x},{y},{width},{height} on {video_width}x{video_height} source"

    output = set_project_crop(project, crop_config=crop_config, output_path=output_path)
    console.print(f"[green]Crop {detail}[/green] in [cyan]{output}[/cyan]")


@main.command("apply-analysis")
@click.argument("manual_project", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("analysis_project", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", "output_path", required=True,
              type=click.Path(dir_okay=False, path_type=Path),
              help="Merged editable .vedproj output path.")
def apply_analysis(manual_project: Path, analysis_project: Path, output_path: Path) -> None:
    """Merge a manual crop/edit project with an analyzed transcript project."""
    output = merge_project_with_analysis(manual_project, analysis_project, output_path)
    console.print(Panel.fit(
        "\n".join([
            "[bold green]Analysis applied[/bold green]",
            f"Manual project: [cyan]{Path(manual_project).expanduser().resolve()}[/cyan]",
            f"Analysis project: [cyan]{Path(analysis_project).expanduser().resolve()}[/cyan]",
            f"Output project: [cyan]{output}[/cyan]",
        ]),
        border_style="green",
    ))


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


@main.command()
@click.argument("project", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--export", "export_video", required=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Exported video to verify.")
@click.option("--analysis", "analysis_path",
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Optional original analysis JSON. Defaults to project metadata if available.")
@click.option("--report", "report_path", type=click.Path(dir_okay=False, path_type=Path),
              help="Machine-readable QC JSON path. Defaults beside the project.")
@click.option("--markdown", "markdown_path", type=click.Path(dir_okay=False, path_type=Path),
              help="Human-readable QC Markdown path. Defaults beside the project.")
@click.option("--review-dir", type=click.Path(file_okay=False, path_type=Path),
              help="Optional directory for source review clips around QC issues.")
@click.option("--skip-export-transcription", is_flag=True,
              help="Skip independent transcription of the exported video.")
@click.option("--silent-visual-min-gap", type=float, default=2.0, show_default=True,
              help="Minimum no-speech gap to check for visual activity.")
@click.option("--visual-motion-threshold", type=float, default=0.2, show_default=True,
              help="Frame-change score required to flag a silent visual range.")
@click.option("--keep-temp", is_flag=True, help="Keep temporary processing files.")
def qc(
    project: Path,
    export_video: Path,
    analysis_path: Path | None,
    report_path: Path | None,
    markdown_path: Path | None,
    review_dir: Path | None,
    skip_export_transcription: bool,
    silent_visual_min_gap: float,
    visual_motion_threshold: float,
    keep_temp: bool,
) -> None:
    """Run second-pass QC against an exported edit."""
    project = project.expanduser().resolve()
    report_path = report_path or _default_qc_report_path(project)
    markdown_path = markdown_path or _default_qc_markdown_path(project)

    result = run_quality_control(
        project_path=project,
        export_video=export_video,
        analysis_path=analysis_path,
        config=_build_config(openai_key=None, keep_temp=keep_temp),
        skip_export_transcription=skip_export_transcription,
        silent_visual_min_gap=silent_visual_min_gap,
        visual_motion_threshold=visual_motion_threshold,
    )
    report_path = write_qc_json(result, report_path)
    markdown_path = write_qc_markdown(result, markdown_path)
    clips = write_qc_review_clips(result, review_dir)
    summary = issue_summary(result.issues)

    console.print(Panel.fit(
        "\n".join([
            f"[bold]{'QC status'}:[/bold] {result.status}",
            f"Project: [cyan]{project}[/cyan]",
            f"Export: [cyan]{Path(export_video).expanduser().resolve()}[/cyan]",
            f"QC JSON: [cyan]{report_path}[/cyan]",
            f"QC Markdown: [cyan]{markdown_path}[/cyan]",
            f"Issues: {summary['issue_count']} "
            f"(high={summary['high']}, medium={summary['medium']}, low={summary['low']})",
            f"Review: unresolved={summary['unresolved_review']}, "
            f"accepted={summary['accepted_style']}, "
            f"likely_false_positive={summary['likely_false_positive']}",
            f"Expected/export transcript similarity: {result.expected_actual_similarity:.1f}",
            *([f"Review clips: [cyan]{review_dir}[/cyan] ({len(clips)})"] if review_dir else []),
        ]),
        border_style="yellow" if result.status != "pass" else "green",
    ))


@main.command("review-silence")
@click.argument("project", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out-dir", type=click.Path(file_okay=False, path_type=Path),
              help="Directory for cropped contact sheets. Defaults beside the project.")
@click.option("--report", "report_path", type=click.Path(dir_okay=False, path_type=Path),
              help="Machine-readable JSON manifest path.")
@click.option("--markdown", "markdown_path", type=click.Path(dir_okay=False, path_type=Path),
              help="Human-readable Markdown review path.")
@click.option("--silent-visual-min-gap", type=float, default=2.0, show_default=True,
              help="Minimum no-speech gap to check for visual activity.")
@click.option("--visual-motion-threshold", type=float, default=0.2, show_default=True,
              help="Frame-change score required to flag a silent visual range.")
@click.option("--keep-temp", is_flag=True, help="Keep temporary processing files.")
def review_silence(
    project: Path,
    out_dir: Path | None,
    report_path: Path | None,
    markdown_path: Path | None,
    silent_visual_min_gap: float,
    visual_motion_threshold: float,
    keep_temp: bool,
) -> None:
    """Generate cropped contact sheets for no-speech ranges with screen motion."""
    project = project.expanduser().resolve()
    out_dir = out_dir or project.with_suffix("").with_name(f"{project.stem}_silent_review")
    report_path = report_path or Path(out_dir) / "silent_visual_review.json"
    markdown_path = markdown_path or Path(out_dir) / "silent_visual_review.md"
    manifest = write_silent_visual_contact_sheets(
        project_path=project,
        output_dir=out_dir,
        min_gap=silent_visual_min_gap,
        motion_threshold=visual_motion_threshold,
        config=_build_config(openai_key=None, keep_temp=keep_temp),
    )
    report_path, markdown_path = write_silent_visual_review_files(
        manifest,
        json_path=report_path,
        markdown_path=markdown_path,
    )
    console.print(Panel.fit(
        "\n".join([
            "[bold green]Silent visual review created[/bold green]",
            f"Project: [cyan]{project}[/cyan]",
            f"Ranges: {len(manifest)}",
            f"Contact sheets: [cyan]{Path(out_dir).expanduser().resolve()}[/cyan]",
            f"JSON report: [cyan]{report_path}[/cyan]",
            f"Markdown report: [cyan]{markdown_path}[/cyan]",
        ]),
        border_style="green",
    ))


@main.command("finish")
@click.argument("input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output-project", type=click.Path(dir_okay=False, path_type=Path),
              help="Editable output .vedproj. Defaults to *_upload_ready.vedproj.")
@click.option("--export-output", type=click.Path(dir_okay=False, path_type=Path),
              help="Exported MP4 path. Defaults to *_upload_ready.mp4.")
@click.option("--analysis-project", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Existing analyzed project to merge with a manual project.")
@click.option("--analysis-report", type=click.Path(dir_okay=False, path_type=Path),
              help="Analysis JSON path to write/use.")
@click.option("--analysis-markdown", type=click.Path(dir_okay=False, path_type=Path),
              help="Analysis Markdown path to write.")
@click.option("--qc-report", type=click.Path(dir_okay=False, path_type=Path),
              help="QC JSON output path.")
@click.option("--qc-markdown", type=click.Path(dir_okay=False, path_type=Path),
              help="QC Markdown output path.")
@click.option("--review-dir", type=click.Path(file_okay=False, path_type=Path),
              help="Directory for QC review clips.")
@click.option("--package-dir", type=click.Path(file_okay=False, path_type=Path),
              help="Optional upload-ready package directory.")
@click.option("--write-back/--no-write-back", default=False, show_default=True,
              help="Replace the input .vedproj with the finished editable project after QC.")
@click.option("--backup/--no-backup", default=True, show_default=True,
              help="When writing back, save a timestamped backup first.")
@click.option("--strict/--conservative", default=True, show_default=True,
              help="Strict analysis uses wider word-boundary padding and stronger review flags.")
@click.option("--no-captions", is_flag=True, help="Skip burned captions.")
@click.option("--skip-export-transcription", is_flag=True,
              help="Skip independent transcription during QC.")
@click.option("--max-fix-passes", type=int, default=2, show_default=True,
              help="Maximum automatic mechanical boundary-fix passes.")
@click.option("--openai-key", envvar="OPENAI_API_KEY",
              help="OpenAI API key fallback for take selection.")
@click.option("--keep-temp", is_flag=True, help="Keep temporary processing files.")
def finish(
    input_path: Path,
    output_project: Path | None,
    export_output: Path | None,
    analysis_project: Path | None,
    analysis_report: Path | None,
    analysis_markdown: Path | None,
    qc_report: Path | None,
    qc_markdown: Path | None,
    review_dir: Path | None,
    package_dir: Path | None,
    write_back: bool,
    backup: bool,
    strict: bool,
    no_captions: bool,
    skip_export_transcription: bool,
    max_fix_passes: int,
    openai_key: str | None,
    keep_temp: bool,
) -> None:
    """Analyze, merge, export, QC, and package an upload-ready edit."""
    result = finish_edit(
        input_path,
        config=_build_config(openai_key=openai_key, keep_temp=keep_temp),
        output_project=output_project,
        export_output=export_output,
        analysis_project=analysis_project,
        analysis_json=analysis_report,
        analysis_markdown=analysis_markdown,
        qc_json=qc_report,
        qc_markdown=qc_markdown,
        review_dir=review_dir,
        package_dir=package_dir,
        write_back=write_back,
        backup=backup,
        no_captions=no_captions,
        skip_export_transcription=skip_export_transcription,
        strict=strict,
        max_fix_passes=max_fix_passes,
    )
    summary = issue_summary(result.qc_result.issues)
    console.print(Panel.fit(
        "\n".join([
            f"[bold]{'Finish status'}:[/bold] {result.qc_result.status}",
            f"Editable project: [cyan]{result.project_path}[/cyan]",
            f"Export: [cyan]{result.export_path}[/cyan]",
            f"QC JSON: [cyan]{result.qc_json_path}[/cyan]",
            f"QC Markdown: [cyan]{result.qc_markdown_path}[/cyan]",
            f"QC: high={summary['high']}, unresolved={summary['unresolved_review']}, "
            f"accepted={summary['accepted_style']}, likely_false_positive={summary['likely_false_positive']}",
            f"Transcript similarity: {result.qc_result.expected_actual_similarity:.1f}",
            f"Boundary repair passes: {result.boundary_repair_passes}",
            *([f"Backup: [cyan]{result.backup_path}[/cyan]"] if result.backup_path else []),
            *([f"Package: [cyan]{result.package_dir}[/cyan]"] if result.package_dir else []),
        ]),
        border_style="green" if result.qc_result.status == "pass" else "yellow",
    ))


@main.command("repair-boundaries")
@click.argument("project", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path),
              help="Project output path. Defaults to updating the project in place.")
def repair_boundaries(project: Path, output_path: Path | None) -> None:
    """Rewrite project keep ranges so cuts do not cross source word tokens."""
    output, changes = repair_project_boundaries(project, output_path=output_path)
    console.print(
        f"[green]Repaired boundaries[/green] in [cyan]{output}[/cyan] "
        f"({changes} range(s) adjusted)"
    )


if __name__ == "__main__":
    main()
