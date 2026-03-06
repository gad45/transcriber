#!/usr/bin/env python3
"""Build a standalone macOS app bundle and DMG for Video Editor."""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


APP_NAME = "Video Editor"
BUNDLE_ID = "com.videoeditoragent.videoeditor"
ROOT_DIR = Path(__file__).resolve().parents[2]
BUILD_DIR = ROOT_DIR / "build" / "macos"
DIST_DIR = ROOT_DIR / "dist" / "macos"
HELPER_SOURCE = ROOT_DIR / "video_editor" / "gui" / "recorder" / "macos_system_audio_helper.swift"
ENTRYPOINT = ROOT_DIR / "video_editor" / "gui_main.py"


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd or ROOT_DIR, check=True)


def _run_quiet(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(
        cmd,
        cwd=cwd or ROOT_DIR,
        check=True,
        capture_output=True,
        text=True,
    )


def _capture(cmd: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        cmd,
        cwd=cwd or ROOT_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _project_version() -> str:
    pyproject_path = ROOT_DIR / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)
    return str(data["project"]["version"])


def _builder_python() -> Path:
    venv_python = ROOT_DIR / "venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


def _compile_native_helper(output_path: Path) -> None:
    swiftc = shutil.which("swiftc")
    if not swiftc:
        raise RuntimeError("swiftc was not found. Install Xcode or the Xcode command line tools.")
    if not HELPER_SOURCE.exists():
        raise RuntimeError(f"Recorder helper source is missing: {HELPER_SOURCE}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            swiftc,
            "-parse-as-library",
            "-O",
            str(HELPER_SOURCE),
            "-o",
            str(output_path),
        ]
    )
    output_path.chmod(0o755)


def _safe_remove(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _is_system_library(path_str: str) -> bool:
    return path_str.startswith("/System/Library/") or path_str.startswith("/usr/lib/")


def _otool_dependencies(path: Path) -> list[str]:
    lines = _capture(["otool", "-L", str(path)]).splitlines()
    dependencies: list[str] = []
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        dependency = stripped.split(" (compatibility version", 1)[0].strip()
        dependencies.append(dependency)
    return dependencies


def _resolve_dependency_path(path_str: str, source_path: Path) -> Path | None:
    if path_str.startswith("@loader_path/"):
        return (source_path.parent / path_str.removeprefix("@loader_path/")).resolve()
    if path_str.startswith("@executable_path/"):
        return (source_path.parent / path_str.removeprefix("@executable_path/")).resolve()
    if path_str.startswith("@rpath/"):
        candidate = source_path.parent / path_str.removeprefix("@rpath/")
        if candidate.exists():
            return candidate.resolve()
        return None
    candidate = Path(path_str)
    if candidate.exists():
        return candidate.resolve()
    return None


def _rewrite_binary_dependencies(
    target_path: Path,
    *,
    relative_lib_dir: str,
    local_library_names: set[str],
) -> None:
    install_name_tool = shutil.which("install_name_tool")
    if not install_name_tool:
        raise RuntimeError("install_name_tool was not found.")

    if target_path.suffix == ".dylib":
        _run_quiet(
            [
                install_name_tool,
                "-id",
                f"@loader_path/{target_path.name}",
                str(target_path),
            ]
        )

    for dependency in _otool_dependencies(target_path):
        if dependency.startswith("@loader_path/"):
            continue
        if _is_system_library(dependency):
            continue
        dep_name = Path(dependency).name
        if dep_name not in local_library_names:
            continue
        _run_quiet(
            [
                install_name_tool,
                "-change",
                dependency,
                f"{relative_lib_dir}{dep_name}",
                str(target_path),
            ]
        )


def _default_ffmpeg_bins() -> tuple[Path | None, Path | None]:
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    return (
        Path(ffmpeg_path).resolve() if ffmpeg_path else None,
        Path(ffprobe_path).resolve() if ffprobe_path else None,
    )


def _default_env_file() -> Path | None:
    env_path = ROOT_DIR / ".env"
    if env_path.exists():
        return env_path.resolve()
    return None


def _bundle_ffmpeg_tree(app_path: Path, ffmpeg_bin: Path, ffprobe_bin: Path) -> None:
    frameworks_dir = app_path / "Contents" / "Frameworks"
    bin_dir = frameworks_dir / "bin"
    lib_dir = frameworks_dir / "lib"
    bin_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = ffmpeg_bin.resolve()
    ffprobe_bin = ffprobe_bin.resolve()
    binaries = [ffmpeg_bin, ffprobe_bin]

    copied_libraries: dict[Path, Path] = {}
    local_library_names: set[str] = set()
    queue: list[Path] = binaries.copy()

    while queue:
        current = queue.pop(0)
        for dependency in _otool_dependencies(current):
            if _is_system_library(dependency):
                continue
            resolved_dependency = _resolve_dependency_path(dependency, current)
            if resolved_dependency is None or not resolved_dependency.exists():
                raise RuntimeError(f"Unable to resolve dependency {dependency} for {current}")
            dependency_name = Path(dependency).name
            target_path = lib_dir / resolved_dependency.name
            local_library_names.add(resolved_dependency.name)
            if dependency_name:
                local_library_names.add(dependency_name)

            if resolved_dependency not in copied_libraries:
                shutil.copy2(resolved_dependency, target_path)
                target_path.chmod(0o755)
                copied_libraries[resolved_dependency] = target_path
                queue.append(resolved_dependency)

            if dependency_name and dependency_name != resolved_dependency.name:
                alias_path = lib_dir / dependency_name
                if not alias_path.exists():
                    alias_path.symlink_to(target_path.name)

    bundled_binary_paths: list[Path] = []
    for binary in binaries:
        target_path = bin_dir / binary.name
        shutil.copy2(binary, target_path)
        target_path.chmod(0o755)
        bundled_binary_paths.append(target_path)

    for library_path in copied_libraries.values():
        _rewrite_binary_dependencies(
            library_path,
            relative_lib_dir="@loader_path/",
            local_library_names=local_library_names,
        )

    for bundled_binary in bundled_binary_paths:
        _rewrite_binary_dependencies(
            bundled_binary,
            relative_lib_dir="@loader_path/../lib/",
            local_library_names=local_library_names,
        )


def _update_info_plist(app_path: Path, version: str) -> None:
    plist_path = app_path / "Contents" / "Info.plist"
    with plist_path.open("rb") as f:
        info = plistlib.load(f)

    info.update(
        {
            "CFBundleDisplayName": APP_NAME,
            "CFBundleName": APP_NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "LSApplicationCategoryType": "public.app-category.video",
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": (
                "Video Editor needs microphone access to record audio input."
            ),
            "NSScreenCaptureUsageDescription": (
                "Video Editor needs screen capture access to record your display."
            ),
            "NSAudioCaptureUsageDescription": (
                "Video Editor needs system audio access to record macOS output audio."
            ),
        }
    )

    with plist_path.open("wb") as f:
        plistlib.dump(info, f, sort_keys=False)


def _codesign(app_path: Path, identity: str) -> None:
    _run(
        [
            "codesign",
            "--force",
            "--deep",
            "--sign",
            identity,
            "--timestamp=none",
            str(app_path),
        ]
    )


def _verify_signature(app_path: Path) -> None:
    _run(["codesign", "--verify", "--deep", "--strict", str(app_path)])


def _create_dmg(app_path: Path, dmg_path: Path) -> None:
    dmg_root = BUILD_DIR / "dmg-root"
    _safe_remove(dmg_root)
    dmg_root.mkdir(parents=True, exist_ok=True)

    staged_app = dmg_root / app_path.name
    shutil.copytree(app_path, staged_app, symlinks=True)
    os.symlink("/Applications", dmg_root / "Applications")

    _safe_remove(dmg_path)
    _run(
        [
            "hdiutil",
            "create",
            "-volname",
            APP_NAME,
            "-srcfolder",
            str(dmg_root),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ]
    )


def _pyinstaller_command(
    helper_binary: Path,
    *,
    dist_dir: Path,
    work_dir: Path,
    env_file: Path | None,
) -> list[str]:
    python = _builder_python()
    cmd = [
        str(python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        APP_NAME,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(work_dir),
        "--osx-bundle-identifier",
        BUNDLE_ID,
        "--add-binary",
        f"{helper_binary}:bin",
    ]

    if env_file is not None:
        cmd += ["--add-data", f"{env_file}:."]

    cmd.append(str(ENTRYPOINT))
    return cmd


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ffmpeg-bin",
        type=Path,
        help="Optional FFmpeg binary to bundle inside the app.",
    )
    parser.add_argument(
        "--ffprobe-bin",
        type=Path,
        help="Optional FFprobe binary to bundle inside the app.",
    )
    parser.add_argument(
        "--no-bundle-ffmpeg",
        action="store_true",
        help="Skip bundling FFmpeg and FFprobe into the app.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional .env file to bundle inside the app.",
    )
    parser.add_argument(
        "--no-bundle-env",
        action="store_true",
        help="Skip bundling a .env file into the app.",
    )
    parser.add_argument(
        "--codesign-identity",
        default="-",
        help="codesign identity. Defaults to ad-hoc signing ('-').",
    )
    parser.add_argument(
        "--no-codesign",
        action="store_true",
        help="Skip codesigning the finished app bundle.",
    )
    parser.add_argument(
        "--no-dmg",
        action="store_true",
        help="Build the .app only and skip DMG creation.",
    )
    return parser.parse_args()


def main() -> int:
    if sys.platform != "darwin":
        raise SystemExit("This build script only runs on macOS.")

    args = _parse_args()

    ffmpeg_bin = args.ffmpeg_bin.resolve() if args.ffmpeg_bin else None
    ffprobe_bin = args.ffprobe_bin.resolve() if args.ffprobe_bin else None

    if (ffmpeg_bin is None) != (ffprobe_bin is None):
        raise SystemExit("Pass both --ffmpeg-bin and --ffprobe-bin together, or neither.")

    if not args.no_bundle_ffmpeg and ffmpeg_bin is None and ffprobe_bin is None:
        ffmpeg_bin, ffprobe_bin = _default_ffmpeg_bins()

    bundle_ffmpeg = not args.no_bundle_ffmpeg and ffmpeg_bin is not None and ffprobe_bin is not None
    env_file = args.env_file.resolve() if args.env_file else None
    if env_file is not None and not env_file.exists():
        raise SystemExit(f"Env file not found: {env_file}")
    if not args.no_bundle_env and env_file is None:
        env_file = _default_env_file()
    bundle_env = not args.no_bundle_env and env_file is not None

    version = _project_version()
    build_root = BUILD_DIR / "pyinstaller"
    native_bin_dir = BUILD_DIR / "bin"
    helper_binary = native_bin_dir / "macos_system_audio_helper"
    app_path = DIST_DIR / f"{APP_NAME}.app"
    dmg_path = DIST_DIR / f"VideoEditor-{version}.dmg"

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    _compile_native_helper(helper_binary)
    _safe_remove(app_path)
    _run(
        _pyinstaller_command(
            helper_binary,
            dist_dir=DIST_DIR,
            work_dir=build_root,
            env_file=env_file if bundle_env else None,
        )
    )

    if not app_path.exists():
        raise RuntimeError(f"PyInstaller did not produce the expected app bundle: {app_path}")

    if bundle_ffmpeg:
        _bundle_ffmpeg_tree(app_path, ffmpeg_bin, ffprobe_bin)

    _update_info_plist(app_path, version)

    if not args.no_codesign:
        _codesign(app_path, args.codesign_identity)
        _verify_signature(app_path)

    if not args.no_dmg:
        _create_dmg(app_path, dmg_path)

    print(f"App bundle: {app_path}")
    if not args.no_dmg:
        print(f"DMG: {dmg_path}")
    else:
        print("DMG: skipped")

    if bundle_ffmpeg:
        print(f"Bundled FFmpeg: {ffmpeg_bin}")
        print(f"Bundled FFprobe: {ffprobe_bin}")
    else:
        print("Bundled FFmpeg: no")

    if bundle_env:
        print(f"Bundled .env: {env_file}")
    else:
        print("Bundled .env: no")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
