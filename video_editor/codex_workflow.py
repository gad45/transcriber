"""Higher-level Codex workflows built on the headless video editor APIs."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .analysis_pipeline import (
    analyze_recording,
    write_analysis_json,
    write_analysis_markdown,
    write_analysis_project,
)
from .config import Config
from .export_pipeline import export_project
from .project_io import (
    backup_project_file,
    format_time,
    load_project,
    merge_project_with_analysis,
)
from .qc_pipeline import (
    QCResult,
    issue_summary,
    repair_project_boundaries,
    run_quality_control,
    write_qc_json,
    write_qc_markdown,
    write_qc_review_clips,
)


MECHANICAL_HIGH_ISSUES = {
    "cut_inside_token",
    "unsafe_start_boundary",
    "unsafe_end_boundary",
}


@dataclass
class FinishResult:
    """Artifacts created by the finish workflow."""

    project_path: Path
    export_path: Path
    qc_json_path: Path
    qc_markdown_path: Path
    qc_result: QCResult
    analysis_project_path: Path | None = None
    analysis_json_path: Path | None = None
    analysis_markdown_path: Path | None = None
    backup_path: Path | None = None
    review_dir: Path | None = None
    package_dir: Path | None = None
    boundary_repair_passes: int = 0


def upload_ready_stem(path: Path) -> str:
    """Return a stable stem for upload-ready derivatives."""
    stem = Path(path).expanduser().resolve().with_suffix("")
    return str(stem)


def default_upload_paths(path: Path) -> dict[str, Path]:
    """Return default output paths for a video or project."""
    base = Path(upload_ready_stem(path))
    parent = base.parent
    name = base.name
    return {
        "project": parent / f"{name}_upload_ready.vedproj",
        "export": parent / f"{name}_upload_ready.mp4",
        "qc_json": parent / f"{name}_upload_ready.qc.json",
        "qc_markdown": parent / f"{name}_upload_ready.qc.md",
        "review_dir": parent / f"{name}_upload_ready_qc_clips",
        "analysis_project": parent / f"{name}_ai.vedproj",
        "analysis_json": parent / f"{name}.analysis.json",
        "analysis_markdown": parent / f"{name}.analysis.md",
        "package_dir": parent / f"{name}_upload-ready",
    }


def _project_has_analysis(project_path: Path) -> bool:
    project = load_project(project_path)
    return bool(project.segments and project.tokens and project.analyzed and project.original_keep_ranges)


def _copy_project(source: Path, destination: Path) -> Path:
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if source == destination:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def _write_analysis_for_video(
    video_path: Path,
    *,
    project_path: Path,
    json_path: Path,
    markdown_path: Path,
    config: Config,
    strict: bool,
) -> tuple[Path, Path, Path]:
    result = analyze_recording(video_path, config=config, strict=strict)
    json_path = write_analysis_json(result, json_path)
    markdown_path = write_analysis_markdown(result, markdown_path)
    project_path = write_analysis_project(
        result,
        project_path,
        metadata={
            "json_report": str(json_path),
            "markdown_report": str(markdown_path),
            "strict": strict,
        },
    )
    return project_path, json_path, markdown_path


def _has_mechanical_high_issues(result: QCResult) -> bool:
    return any(
        issue.severity == "high" and issue.type in MECHANICAL_HIGH_ISSUES
        for issue in result.issues
    )


def _write_package_manifest(result: FinishResult, package_dir: Path) -> Path:
    package_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "project_path": str(result.project_path),
        "export_path": str(result.export_path),
        "qc_json_path": str(result.qc_json_path),
        "qc_markdown_path": str(result.qc_markdown_path),
        "analysis_project_path": str(result.analysis_project_path) if result.analysis_project_path else None,
        "analysis_json_path": str(result.analysis_json_path) if result.analysis_json_path else None,
        "analysis_markdown_path": str(result.analysis_markdown_path) if result.analysis_markdown_path else None,
        "backup_path": str(result.backup_path) if result.backup_path else None,
        "review_dir": str(result.review_dir) if result.review_dir else None,
        "qc_status": result.qc_result.status,
        "qc_summary": issue_summary(result.qc_result.issues),
        "export_duration": result.qc_result.export_duration,
        "export_duration_formatted": format_time(result.qc_result.export_duration),
        "source_video": str(result.qc_result.source_video),
        "expected_actual_similarity": result.qc_result.expected_actual_similarity,
        "independent_export_transcription": bool(result.qc_result.actual_transcript),
        "boundary_repair_passes": result.boundary_repair_passes,
    }
    manifest_path = package_dir / "source_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def finish_edit(
    input_path: Path,
    *,
    config: Config,
    output_project: Path | None = None,
    export_output: Path | None = None,
    analysis_project: Path | None = None,
    analysis_json: Path | None = None,
    analysis_markdown: Path | None = None,
    qc_json: Path | None = None,
    qc_markdown: Path | None = None,
    review_dir: Path | None = None,
    package_dir: Path | None = None,
    write_back: bool = False,
    backup: bool = True,
    no_captions: bool = False,
    skip_export_transcription: bool = False,
    strict: bool = True,
    max_fix_passes: int = 2,
) -> FinishResult:
    """Analyze/merge/export/QC a recording or project into upload-ready artifacts."""
    input_path = Path(input_path).expanduser().resolve()
    defaults = default_upload_paths(input_path)
    working_project = Path(output_project or defaults["project"]).expanduser().resolve()
    export_path = Path(export_output or defaults["export"]).expanduser().resolve()
    qc_json_path = Path(qc_json or defaults["qc_json"]).expanduser().resolve()
    qc_markdown_path = Path(qc_markdown or defaults["qc_markdown"]).expanduser().resolve()
    review_path = (
        Path(review_dir or defaults["review_dir"]).expanduser().resolve()
        if review_dir or package_dir else None
    )
    package_path = Path(package_dir or defaults["package_dir"]).expanduser().resolve() if package_dir else None
    backup_path: Path | None = None

    created_analysis_project: Path | None = None
    created_analysis_json: Path | None = Path(analysis_json).expanduser().resolve() if analysis_json else None
    created_analysis_markdown: Path | None = Path(analysis_markdown).expanduser().resolve() if analysis_markdown else None

    if input_path.suffix.lower() == ".vedproj":
        source_project = input_path
        if analysis_project:
            created_analysis_project = Path(analysis_project).expanduser().resolve()
            working_project = merge_project_with_analysis(source_project, created_analysis_project, working_project)
        elif _project_has_analysis(source_project):
            working_project = _copy_project(source_project, working_project)
        else:
            source = load_project(source_project)
            created_analysis_project = Path(analysis_project or defaults["analysis_project"]).expanduser().resolve()
            created_analysis_json = created_analysis_json or defaults["analysis_json"]
            created_analysis_markdown = created_analysis_markdown or defaults["analysis_markdown"]
            created_analysis_project, created_analysis_json, created_analysis_markdown = _write_analysis_for_video(
                source.video_path.expanduser().resolve(),
                project_path=created_analysis_project,
                json_path=created_analysis_json,
                markdown_path=created_analysis_markdown,
                config=config,
                strict=strict,
            )
            working_project = merge_project_with_analysis(source_project, created_analysis_project, working_project)
    else:
        created_analysis_project = working_project
        created_analysis_json = created_analysis_json or defaults["analysis_json"]
        created_analysis_markdown = created_analysis_markdown or defaults["analysis_markdown"]
        working_project, created_analysis_json, created_analysis_markdown = _write_analysis_for_video(
            input_path,
            project_path=working_project,
            json_path=created_analysis_json,
            markdown_path=created_analysis_markdown,
            config=config,
            strict=strict,
        )

    boundary_repair_passes = 0
    repair_project_boundaries(working_project, output_path=working_project)
    boundary_repair_passes += 1

    qc_result: QCResult | None = None
    for _ in range(max(1, max_fix_passes + 1)):
        project = load_project(working_project)
        export_project(project, export_path, config=config, no_captions=no_captions)
        qc_result = run_quality_control(
            project_path=working_project,
            export_video=export_path,
            analysis_path=created_analysis_json,
            config=config,
            skip_export_transcription=skip_export_transcription,
        )
        write_qc_json(qc_result, qc_json_path)
        write_qc_markdown(qc_result, qc_markdown_path)
        write_qc_review_clips(qc_result, review_path)

        if qc_result.status != "needs_fix" or not _has_mechanical_high_issues(qc_result):
            break
        repair_project_boundaries(working_project, output_path=working_project)
        boundary_repair_passes += 1

    if qc_result is None:
        raise RuntimeError("finish workflow did not produce a QC result")

    final_project = working_project
    if write_back and input_path.suffix.lower() == ".vedproj":
        if backup:
            backup_path = backup_project_file(input_path)
        _copy_project(working_project, input_path)
        final_project = input_path

    result = FinishResult(
        project_path=final_project,
        export_path=export_path,
        qc_json_path=qc_json_path,
        qc_markdown_path=qc_markdown_path,
        qc_result=qc_result,
        analysis_project_path=created_analysis_project,
        analysis_json_path=created_analysis_json,
        analysis_markdown_path=created_analysis_markdown,
        backup_path=backup_path,
        review_dir=review_path,
        package_dir=package_path,
        boundary_repair_passes=boundary_repair_passes,
    )

    if package_path:
        _write_package_manifest(result, package_path)
        for source, name in (
            (result.export_path, "final.mp4"),
            (result.project_path, "editable.vedproj"),
            (result.qc_markdown_path, "qc.md"),
            (result.qc_json_path, "qc.json"),
        ):
            if source and Path(source).exists():
                shutil.copy2(source, package_path / name)
        if result.review_dir and result.review_dir.exists():
            package_review_dir = package_path / "review_clips"
            if package_review_dir.exists():
                shutil.rmtree(package_review_dir)
            shutil.copytree(result.review_dir, package_review_dir)

    return result
