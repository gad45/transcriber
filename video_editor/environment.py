"""Environment loading helpers for bundled and source runs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

from .runtime_paths import resolve_bundled_resource


def _iter_env_candidates() -> list[Path]:
    candidates: list[Path] = []

    bundled_env = resolve_bundled_resource(".env")
    if bundled_env is not None:
        candidates.append(bundled_env)

    repo_env = Path(__file__).resolve().parents[1] / ".env"
    candidates.append(repo_env)

    candidates.append(Path.cwd() / ".env")

    if getattr(sys, "frozen", False):
        executable_path = Path(sys.executable).resolve()
        app_bundle_dir = executable_path.parents[2]
        candidates.append(app_bundle_dir.parent / ".env")

    env_override = os.getenv("VIDEO_EDITOR_ENV_PATH", "").strip()
    if env_override:
        candidates.append(Path(env_override).expanduser())

    seen: set[str] = set()
    unique_candidates: list[Path] = []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)
    return unique_candidates


def load_app_env() -> list[Path]:
    """Load environment variables from bundled and local `.env` files."""
    loaded_paths: list[Path] = []
    merged_values: dict[str, str] = {}

    for candidate in _iter_env_candidates():
        if not candidate.exists():
            continue

        values = {
            key: value
            for key, value in dotenv_values(candidate).items()
            if value is not None
        }
        if values:
            merged_values.update(values)
            loaded_paths.append(candidate)

    for key, value in merged_values.items():
        os.environ.setdefault(key, value)

    return loaded_paths
