# Repository Guidelines

## Project Structure & Module Organization
- `video_editor/` is the main Python package.
- `video_editor/gui/` contains the PySide6 (Qt) GUI implementation, including editor and recorder tabs.
- `test/` holds sample media (`.mp4`) and project files (`.vedproj`) used for manual regression checks.
- `pyproject.toml` defines the package, dependencies, and console scripts (`video-editor`, `video-editor-gui`).
- `launch_gui.command` and `launch_gui.bat` are convenience launchers for macOS/Windows.

## Build, Test, and Development Commands
- Create env and install:
  - `python3 -m venv venv`
  - `source venv/bin/activate`
  - `pip install -e .`
- Run CLI editor:
  - `python -m video_editor input.mp4 -o output.mp4`
  - `python -m video_editor input.mp4 --preview`
- Run GUI:
  - `python -m video_editor.gui_main` (optionally pass a video path)
- Tests (when present):
  - `pytest`

## Coding Style & Naming Conventions
- Python 3.10+ with standard 4-space indentation.
- Use type hints where practical (existing code does).
- Naming follows common Python conventions: `snake_case` for modules/functions, `CamelCase` for classes, constants in `UPPER_SNAKE_CASE`.
- No enforced formatter or linter config is checked in; keep edits stylistically consistent with nearby files.

## Testing Guidelines
- Automated tests are not currently committed; `pytest` dependencies are available for future tests.
- Prefer manual checks with sample assets, e.g.:
  - `python -m video_editor.gui_main test/test.mp4`
- When adding tests, keep filenames `test_*.py` or `*_test.py` for `pytest` discovery.

## Commit & Pull Request Guidelines
- Commit messages in history use short, imperative, sentence-case subjects (e.g., “Add caption styling options”).
- Keep commits focused and avoid bundling unrelated changes.
- PRs should include a concise summary, key behavior changes, and how you tested.
- For GUI changes, include screenshots or short screen recordings when visual behavior changes.

## Configuration & Security
- FFmpeg must be installed and available on `PATH`.
- API keys can be provided via `.env` in the repo root:
  - `SONIOX_API_KEY` (required for transcription)
  - `GEMINI_API_KEY` (recommended for take selection)
- GUI settings can also store keys in `~/.video_editor_settings`; avoid committing secrets.
