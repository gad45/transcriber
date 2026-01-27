@echo off
REM AI Video Editor GUI Launcher for Windows
REM Double-click this file to launch the GUI

cd /d "%~dp0"

REM Check if virtual environment exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo Virtual environment not found. Please run:
    echo   python -m venv venv
    echo   venv\Scripts\activate
    echo   pip install -e .
    pause
    exit /b 1
)

REM Launch the GUI
python -m video_editor.gui_main %*

REM Keep window open if there was an error
if errorlevel 1 (
    echo.
    echo An error occurred. Press any key to close...
    pause >nul
)
