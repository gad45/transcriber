#!/bin/bash
# Video Editor GUI Launcher
# Double-click this file to launch the GUI

cd "$(dirname "$0")"
source venv/bin/activate
python -m video_editor.gui_main "$@"
