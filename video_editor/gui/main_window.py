"""Main window for the video editor GUI."""

import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QMenuBar, QMenu, QToolBar, QPushButton, QLabel, QStatusBar,
    QFileDialog, QMessageBox, QProgressDialog, QApplication, QComboBox
)
from PySide6.QtGui import QAction, QKeySequence, QShortcut

from .video_player import VideoPlayer
from .timeline import Timeline
from .transcript_editor import TranscriptEditor
from .models import EditSession, CropConfig
from ..transcriber import Transcriber, Segment
from ..analyzer import Analyzer, AnalyzedSegment, SegmentAction
from ..cutter import Cutter
from ..captioner import Captioner
from ..config import Config


class MainWindow(QMainWindow):
    """
    Main application window for the video editor GUI.

    Layout:
    - Top: Menu bar
    - Left: Video player
    - Right: Transcript editor
    - Bottom: Timeline
    - Status bar: Statistics
    """

    def __init__(self, video_path: Path | None = None, parent=None):
        super().__init__(parent)

        self._session: EditSession | None = None
        self._config = Config()
        self._project_path: Path | None = None
        self._unsaved_changes = False

        self.setWindowTitle("Video Editor")
        self.setMinimumSize(1200, 800)

        self._setup_ui()
        self._setup_menu()
        self._setup_shortcuts()
        self._connect_signals()

        # Apply dark theme
        self._apply_dark_theme()

        if video_path:
            QTimer.singleShot(100, lambda: self._load_video(video_path))

    def _setup_ui(self):
        """Set up the main UI layout."""
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Toolbar
        self._toolbar = QToolBar()
        self._toolbar.setMovable(False)
        self._setup_toolbar()
        main_layout.addWidget(self._toolbar)

        # Main content splitter (video + transcript)
        content_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Video player (left)
        self._video_player = VideoPlayer()
        content_splitter.addWidget(self._video_player)

        # Transcript editor (right)
        self._transcript_editor = TranscriptEditor()
        content_splitter.addWidget(self._transcript_editor)

        content_splitter.setSizes([600, 400])
        main_layout.addWidget(content_splitter, stretch=1)

        # Timeline (bottom)
        self._timeline = Timeline()
        main_layout.addWidget(self._timeline)

        # Status bar
        self._status_bar = QStatusBar()
        self._status_label = QLabel("No video loaded")
        self._status_bar.addPermanentWidget(self._status_label)
        self.setStatusBar(self._status_bar)

    def _setup_toolbar(self):
        """Set up the toolbar buttons."""
        # Open Video button
        self._open_btn = QPushButton("Open Video")
        self._toolbar.addWidget(self._open_btn)

        self._toolbar.addSeparator()

        # Original / Preview toggle
        self._view_original_btn = QPushButton("Original")
        self._view_original_btn.setCheckable(True)
        self._view_original_btn.setChecked(True)
        self._toolbar.addWidget(self._view_original_btn)

        self._view_preview_btn = QPushButton("Preview")
        self._view_preview_btn.setCheckable(True)
        self._toolbar.addWidget(self._view_preview_btn)

        self._toolbar.addSeparator()

        # Process button
        self._process_btn = QPushButton("Analyze Video")
        self._process_btn.setEnabled(False)
        self._toolbar.addWidget(self._process_btn)

        # Export button
        self._export_btn = QPushButton("Export")
        self._export_btn.setEnabled(False)
        self._toolbar.addWidget(self._export_btn)

        self._toolbar.addSeparator()

        # Crop controls
        self._crop_btn = QPushButton("Crop")
        self._crop_btn.setCheckable(True)
        self._crop_btn.setToolTip("Toggle crop editing mode (C)")
        self._toolbar.addWidget(self._crop_btn)

        # Aspect ratio dropdown
        self._aspect_combo = QComboBox()
        self._aspect_combo.addItems([
            "Free",
            "16:9 (Landscape)",
            "9:16 (Portrait)",
            "1:1 (Square)",
            "4:3 (Standard)",
            "4:5 (Instagram)"
        ])
        self._aspect_combo.setToolTip("Lock aspect ratio")
        self._aspect_combo.setFixedWidth(120)
        self._toolbar.addWidget(self._aspect_combo)

        # Reset crop button
        self._reset_crop_btn = QPushButton("Reset Crop")
        self._reset_crop_btn.setToolTip("Reset to full frame (Shift+R)")
        self._toolbar.addWidget(self._reset_crop_btn)

        self._toolbar.addSeparator()

        # Spacer
        spacer = QWidget()
        spacer.setFixedWidth(20)
        self._toolbar.addWidget(spacer)

    def _setup_menu(self):
        """Set up the menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")

        open_action = file_menu.addAction("Open Video...")
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_video)

        open_project_action = file_menu.addAction("Open Project...")
        open_project_action.setShortcut(QKeySequence("Ctrl+Shift+O"))
        open_project_action.triggered.connect(self._open_project)

        file_menu.addSeparator()

        save_action = file_menu.addAction("Save Project")
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_project)

        save_as_action = file_menu.addAction("Save Project As...")
        save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_as_action.triggered.connect(self._save_project_as)

        file_menu.addSeparator()

        export_action = file_menu.addAction("Export Video...")
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        export_action.triggered.connect(self._export_video)

        file_menu.addSeparator()

        quit_action = file_menu.addAction("Quit")
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)

        # Edit menu
        edit_menu = menubar.addMenu("Edit")

        undo_action = edit_menu.addAction("Undo")
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        undo_action.setEnabled(False)  # TODO: implement undo

        edit_menu.addSeparator()

        select_all_keep = edit_menu.addAction("Keep All Segments")
        select_all_keep.triggered.connect(self._keep_all_segments)

        select_all_cut = edit_menu.addAction("Cut All Segments")
        select_all_cut.triggered.connect(self._cut_all_segments)

        # View menu
        view_menu = menubar.addMenu("View")

        zoom_in = view_menu.addAction("Zoom In")
        zoom_in.setShortcut(QKeySequence("Ctrl++"))

        zoom_out = view_menu.addAction("Zoom Out")
        zoom_out.setShortcut(QKeySequence("Ctrl+-"))

    def _setup_shortcuts(self):
        """Set up keyboard shortcuts."""
        # Space - play/pause
        space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        space.activated.connect(self._video_player.toggle_play)

        # Left/Right arrows - jump 5 seconds
        left = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        left.activated.connect(lambda: self._video_player.jump_backward(5))

        right = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        right.activated.connect(lambda: self._video_player.jump_forward(5))

        # K - toggle keep/cut for selected segment
        toggle = QShortcut(QKeySequence(Qt.Key.Key_K), self)
        toggle.activated.connect(self._toggle_selected_segment)

        # Up/Down - previous/next segment
        up = QShortcut(QKeySequence(Qt.Key.Key_Up), self)
        up.activated.connect(self._select_previous_segment)

        down = QShortcut(QKeySequence(Qt.Key.Key_Down), self)
        down.activated.connect(self._select_next_segment)

        # C - toggle crop mode
        crop_toggle = QShortcut(QKeySequence(Qt.Key.Key_C), self)
        crop_toggle.activated.connect(self._toggle_crop_mode)

        # Shift+R - reset crop
        reset_crop = QShortcut(QKeySequence("Shift+R"), self)
        reset_crop.activated.connect(self._reset_crop)

        # Shift+Arrow keys - pan crop region
        pan_left = QShortcut(QKeySequence("Shift+Left"), self)
        pan_left.activated.connect(lambda: self._pan_crop(-0.05, 0))

        pan_right = QShortcut(QKeySequence("Shift+Right"), self)
        pan_right.activated.connect(lambda: self._pan_crop(0.05, 0))

        pan_up = QShortcut(QKeySequence("Shift+Up"), self)
        pan_up.activated.connect(lambda: self._pan_crop(0, -0.05))

        pan_down = QShortcut(QKeySequence("Shift+Down"), self)
        pan_down.activated.connect(lambda: self._pan_crop(0, 0.05))

    def _connect_signals(self):
        """Connect signals between components."""
        # Video player
        self._video_player.position_changed.connect(self._on_playback_position_changed)
        self._video_player.duration_changed.connect(self._on_duration_changed)

        # Timeline
        self._timeline.segment_clicked.connect(self._on_timeline_segment_clicked)
        self._timeline.seek_requested.connect(self._on_seek_requested)
        self._timeline.toggle_segment.connect(self._on_toggle_segment)
        self._timeline.highlight_created.connect(self._on_highlight_created)
        self._timeline.highlight_removed.connect(self._on_highlight_removed)

        # Transcript editor
        self._transcript_editor.segment_clicked.connect(self._on_transcript_segment_clicked)
        self._transcript_editor.keep_changed.connect(self._on_segment_keep_changed)
        self._transcript_editor.text_changed.connect(self._on_segment_text_changed)

        # Toolbar buttons
        self._open_btn.clicked.connect(self._open_video)
        self._view_original_btn.clicked.connect(self._on_view_original)
        self._view_preview_btn.clicked.connect(self._on_view_preview)
        self._process_btn.clicked.connect(self._analyze_video)
        self._export_btn.clicked.connect(self._export_video)

        # Crop controls
        self._crop_btn.clicked.connect(self._on_crop_btn_clicked)
        self._aspect_combo.currentIndexChanged.connect(self._on_aspect_ratio_changed)
        self._reset_crop_btn.clicked.connect(self._reset_crop)
        self._video_player.crop_changed.connect(self._on_crop_changed)

    def _apply_dark_theme(self):
        """Apply dark theme styling."""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e1e;
            }
            QMenuBar {
                background-color: #2d2d2d;
                color: #fff;
            }
            QMenuBar::item:selected {
                background-color: #3d3d3d;
            }
            QMenu {
                background-color: #2d2d2d;
                color: #fff;
            }
            QMenu::item:selected {
                background-color: #3d3d3d;
            }
            QToolBar {
                background-color: #2d2d2d;
                border: none;
                spacing: 4px;
                padding: 4px;
            }
            QPushButton {
                background-color: #3d3d3d;
                color: #fff;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
            }
            QPushButton:pressed {
                background-color: #2d2d2d;
            }
            QPushButton:checked {
                background-color: #2196f3;
                border-color: #1976d2;
            }
            QPushButton:disabled {
                background-color: #2d2d2d;
                color: #666;
            }
            QStatusBar {
                background-color: #2d2d2d;
                color: #888;
            }
            QSplitter::handle {
                background-color: #3d3d3d;
            }
            QLabel {
                color: #fff;
            }
            QComboBox {
                background-color: #3d3d3d;
                color: #fff;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QComboBox:hover {
                background-color: #4d4d4d;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #3d3d3d;
                color: #fff;
                selection-background-color: #2196f3;
            }
        """)

    # Public methods

    def load_video(self, path: Path):
        """Load a video file."""
        self._load_video(path)

    # Private methods

    def _load_video(self, path: Path):
        """Load a video file and prepare for editing."""
        path = Path(path)
        if not path.exists():
            QMessageBox.critical(self, "Error", f"File not found: {path}")
            return

        self._video_player.load_video(path)
        self.setWindowTitle(f"Video Editor - {path.name}")
        self._process_btn.setEnabled(True)
        self._status_label.setText(f"Loaded: {path.name}")

        # Create initial session without analysis
        self._session = EditSession(
            video_path=path,
            video_duration=0  # Will be updated when duration is known
        )

    def _analyze_video(self):
        """Run the full analysis pipeline."""
        if not self._session:
            return

        path = self._session.video_path

        # Show progress dialog
        progress = QProgressDialog("Analyzing video...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(10)
        QApplication.processEvents()

        try:
            # Initialize components
            transcriber = Transcriber(self._config)
            analyzer = Analyzer(self._config)
            cutter = Cutter(self._config)

            # Get video duration
            progress.setLabelText("Getting video info...")
            progress.setValue(15)
            QApplication.processEvents()
            video_duration = cutter.get_video_duration(path)

            # Transcribe
            progress.setLabelText("Transcribing speech...")
            progress.setValue(20)
            QApplication.processEvents()
            segments, tokens = transcriber.transcribe_video(path)

            if not segments:
                QMessageBox.warning(self, "Warning", "No speech detected in video!")
                return

            progress.setValue(60)

            # Analyze
            progress.setLabelText("Analyzing segments...")
            QApplication.processEvents()

            # Get analyzed segments with actions
            keep_ranges, kept_segments = analyzer.analyze(segments, video_duration)

            # Create analyzed segment objects
            analyzed_segments = []
            kept_set = {(r.start, r.end) for r in keep_ranges}

            for seg in segments:
                # Check if this segment is in keep_ranges
                is_kept = any(
                    r.start <= seg.start and seg.end <= r.end
                    for r in keep_ranges
                )
                action = SegmentAction.KEEP if is_kept else SegmentAction.REMOVE
                analyzed_segments.append(AnalyzedSegment(
                    segment=seg,
                    action=action,
                    reason="" if is_kept else "Retake or silence"
                ))

            progress.setValue(80)

            # Update session
            self._session = EditSession(
                video_path=path,
                video_duration=video_duration,
                original_segments=segments,
                analyzed_segments=analyzed_segments,
                tokens=tokens,
                original_keep_ranges=keep_ranges
            )

            # Update UI
            progress.setLabelText("Updating display...")
            progress.setValue(90)
            QApplication.processEvents()

            self._timeline.load_session(self._session)
            self._transcript_editor.load_session(self._session)

            progress.setValue(100)

            # Update status
            kept_count = sum(1 for a in analyzed_segments if a.action == SegmentAction.KEEP)
            self._status_label.setText(
                f"{path.name} | {len(segments)} segments | {kept_count} kept | "
                f"{video_duration:.1f}s"
            )

            self._export_btn.setEnabled(True)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Analysis failed: {e}")
        finally:
            progress.close()

    # Slots

    @Slot(int)
    def _on_playback_position_changed(self, position_ms: int):
        """Update timeline and transcript when playback position changes."""
        time_seconds = position_ms / 1000.0
        self._timeline.set_playhead_position(time_seconds)
        self._transcript_editor.highlight_current_time(time_seconds)

    @Slot(int)
    def _on_duration_changed(self, duration_ms: int):
        """Update session when video duration is known."""
        if self._session:
            self._session.video_duration = duration_ms / 1000.0

    @Slot(int)
    def _on_timeline_segment_clicked(self, index: int):
        """Seek to segment when clicked in timeline."""
        if self._session and 0 <= index < len(self._session.original_segments):
            seg = self._session.original_segments[index]
            self._video_player.seek_seconds(seg.start)
            self._transcript_editor.select_segment(index)

    @Slot(float)
    def _on_seek_requested(self, time_seconds: float):
        """Seek video when timeline is clicked."""
        self._video_player.seek_seconds(time_seconds)

    @Slot(int)
    def _on_toggle_segment(self, index: int):
        """Toggle keep/cut for a segment."""
        if self._session:
            current = self._session.is_segment_kept(index)
            self._session.set_segment_kept(index, not current)
            self._timeline.update_segment(index, not current)
            self._transcript_editor.update_segment(index, not current)
            self._unsaved_changes = True

    @Slot(int)
    def _on_transcript_segment_clicked(self, index: int):
        """Seek to segment when clicked in transcript."""
        if self._session and 0 <= index < len(self._session.original_segments):
            seg = self._session.original_segments[index]
            self._video_player.seek_seconds(seg.start)

    @Slot(int, bool)
    def _on_segment_keep_changed(self, index: int, is_kept: bool):
        """Update timeline when segment keep status changes."""
        self._timeline.update_segment(index, is_kept)
        self._unsaved_changes = True

    @Slot(int, str)
    def _on_segment_text_changed(self, index: int, text: str):
        """Track text changes."""
        self._unsaved_changes = True

    @Slot(float, float)
    def _on_highlight_created(self, start_time: float, end_time: float):
        """Handle creation of a new highlight region."""
        if self._session:
            index = self._session.add_highlight(start_time, end_time)
            self._timeline.add_highlight(index, start_time, end_time)
            self._unsaved_changes = True
            self._status_label.setText(
                f"Added highlight: {start_time:.1f}s - {end_time:.1f}s"
            )

    @Slot(int)
    def _on_highlight_removed(self, index: int):
        """Handle removal of a highlight region."""
        if self._session:
            self._session.remove_highlight(index)
            self._timeline.remove_highlight(index)
            self._unsaved_changes = True
            self._status_label.setText("Highlight removed")

    @Slot()
    def _on_view_original(self):
        """Switch to original video view."""
        self._view_original_btn.setChecked(True)
        self._view_preview_btn.setChecked(False)
        if self._session:
            self._video_player.load_video(self._session.video_path)

    @Slot()
    def _on_view_preview(self):
        """Generate and show preview of edited video."""
        self._view_original_btn.setChecked(False)
        self._view_preview_btn.setChecked(True)
        # TODO: Generate preview video and load it

    # Crop controls

    @Slot()
    def _on_crop_btn_clicked(self):
        """Toggle crop editing mode."""
        self._toggle_crop_mode()

    def _toggle_crop_mode(self):
        """Toggle crop editing mode."""
        is_crop_mode = not self._video_player.is_crop_mode()
        self._video_player.set_crop_mode(is_crop_mode)
        self._crop_btn.setChecked(is_crop_mode)

        if is_crop_mode:
            self._status_label.setText("Crop mode: Drag to select, drag edges/corners to adjust, drag inside to move")
        else:
            crop = self._video_player.get_crop_config()
            if crop.is_default:
                self._status_label.setText("Crop: Full frame")
            else:
                w, h = self._video_player.get_video_dimensions()
                x, y, cw, ch = crop.get_crop_rect(w, h)
                self._status_label.setText(f"Crop: {cw}x{ch} at ({x},{y})")

    @Slot(int)
    def _on_aspect_ratio_changed(self, index: int):
        """Handle aspect ratio selection change."""
        aspect_ratios = {
            0: None,           # Free
            1: (16, 9),        # 16:9 Landscape
            2: (9, 16),        # 9:16 Portrait
            3: (1, 1),         # 1:1 Square
            4: (4, 3),         # 4:3 Standard
            5: (4, 5),         # 4:5 Instagram
        }

        ratio = aspect_ratios.get(index)

        # Always update the video player's aspect ratio for mouse selection
        self._video_player.set_aspect_ratio(ratio)

        if ratio is None:
            return  # Free aspect ratio, don't change crop automatically

        video_w, video_h = self._video_player.get_video_dimensions()
        target_ratio = ratio[0] / ratio[1]
        video_ratio = video_w / video_h

        # Calculate crop dimensions to match target aspect ratio
        if target_ratio > video_ratio:
            # Target is wider, crop height
            new_width = 1.0
            new_height = video_ratio / target_ratio
        else:
            # Target is taller, crop width
            new_height = 1.0
            new_width = target_ratio / video_ratio

        crop_config = self._video_player.get_crop_config()
        crop_config.width = new_width
        crop_config.height = new_height
        crop_config.pan_x = 0.0  # Center
        crop_config.pan_y = 0.0

        self._video_player.set_crop_config(crop_config)
        self._unsaved_changes = True

    @Slot(object)
    def _on_crop_changed(self, config):
        """Handle crop configuration changes from video player."""
        if self._session:
            self._session.set_global_crop(config)
            self._unsaved_changes = True

    def _pan_crop(self, pan_x_delta: float, pan_y_delta: float):
        """Adjust pan offset by delta values."""
        self._video_player.adjust_pan(pan_x_delta, pan_y_delta)

    def _reset_crop(self):
        """Reset crop to full frame."""
        self._video_player.reset_crop()
        self._aspect_combo.setCurrentIndex(0)  # Set to "Free"
        if self._session:
            self._session.reset_all_crops()
        self._unsaved_changes = True
        self._status_label.setText("Crop reset to full frame")

    def _toggle_selected_segment(self):
        """Toggle keep/cut for the currently selected segment."""
        if self._transcript_editor._selected_index >= 0:
            self._on_toggle_segment(self._transcript_editor._selected_index)

    def _select_previous_segment(self):
        """Select the previous segment."""
        current = self._transcript_editor._selected_index
        if current > 0:
            self._transcript_editor.select_segment(current - 1)
            self._on_transcript_segment_clicked(current - 1)

    def _select_next_segment(self):
        """Select the next segment."""
        current = self._transcript_editor._selected_index
        if self._session and current < len(self._session.original_segments) - 1:
            self._transcript_editor.select_segment(current + 1)
            self._on_transcript_segment_clicked(current + 1)

    def _keep_all_segments(self):
        """Set all segments to keep."""
        if self._session:
            for i in range(len(self._session.original_segments)):
                self._session.set_segment_kept(i, True)
                self._timeline.update_segment(i, True)
                self._transcript_editor.update_segment(i, True)
            self._unsaved_changes = True

    def _cut_all_segments(self):
        """Set all segments to cut."""
        if self._session:
            for i in range(len(self._session.original_segments)):
                self._session.set_segment_kept(i, False)
                self._timeline.update_segment(i, False)
                self._transcript_editor.update_segment(i, False)
            self._unsaved_changes = True

    # File operations

    def _open_video(self):
        """Open a video file dialog."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Video",
            "",
            "Video Files (*.mp4 *.mov *.avi *.mkv);;All Files (*)"
        )
        if path:
            self._load_video(Path(path))

    def _open_project(self):
        """Open a project file."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Project",
            "",
            "Video Editor Projects (*.vedproj);;All Files (*)"
        )
        if path:
            try:
                self._session = EditSession.load(Path(path))
                self._project_path = Path(path)
                self._video_player.load_video(self._session.video_path)
                self._timeline.load_session(self._session)
                self._transcript_editor.load_session(self._session)

                # Restore crop settings
                if self._session.crop_config:
                    self._video_player.set_crop_config(self._session.crop_config)

                self._export_btn.setEnabled(True)
                self.setWindowTitle(f"Video Editor - {path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to open project: {e}")

    def _save_project(self):
        """Save the current project."""
        if not self._session:
            return

        if self._project_path:
            self._session.save(self._project_path)
            self._unsaved_changes = False
        else:
            self._save_project_as()

    def _save_project_as(self):
        """Save the project with a new name."""
        if not self._session:
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project",
            "",
            "Video Editor Projects (*.vedproj)"
        )
        if path:
            if not path.endswith(".vedproj"):
                path += ".vedproj"
            self._session.save(Path(path))
            self._project_path = Path(path)
            self._unsaved_changes = False
            self.setWindowTitle(f"Video Editor - {path}")

    def _export_video(self):
        """Export the edited video."""
        if not self._session:
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Video",
            str(self._session.video_path.with_suffix("")) + "_edited.mp4",
            "MP4 Video (*.mp4)"
        )
        if not path:
            return

        if not path.endswith(".mp4"):
            path += ".mp4"

        # Show progress dialog
        progress = QProgressDialog("Exporting video...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(10)
        QApplication.processEvents()

        try:
            cutter = Cutter(self._config)
            captioner = Captioner(self._config)

            # Get final ranges with user edits
            keep_ranges = self._session.get_final_keep_ranges(
                self._config.segment_start_buffer,
                self._config.segment_end_buffer
            )

            progress.setLabelText("Cutting video...")
            progress.setValue(30)
            QApplication.processEvents()

            # Prepare crop configuration
            crop_filter = None
            segment_crop_filters = None

            if self._session.crop_config and not self._session.crop_config.is_default:
                # Get video dimensions for crop calculation
                video_w, video_h = cutter.get_video_dimensions(self._session.video_path)
                crop_filter = self._session.crop_config.to_ffmpeg_filter(video_w, video_h)

            # Build per-segment crop overrides
            if self._session.segment_crop_overrides:
                video_w, video_h = cutter.get_video_dimensions(self._session.video_path)
                segment_crop_filters = {
                    idx: crop.to_ffmpeg_filter(video_w, video_h)
                    for idx, crop in self._session.segment_crop_overrides.items()
                }

            # Cut to temp file with crop applied
            temp_cut = Path(tempfile.gettempdir()) / "video_editor_temp_cut.mp4"
            cutter.cut_video(
                self._session.video_path,
                keep_ranges,
                temp_cut,
                crop_filter=crop_filter,
                segment_crop_filters=segment_crop_filters
            )

            progress.setLabelText("Adding captions...")
            progress.setValue(70)
            QApplication.processEvents()

            # Get tokens with text edits applied
            tokens = self._session.get_final_tokens()

            if tokens:
                # Adjust token times for the cut video (accounting for gaps between segments)
                from ..main import _adjust_tokens_for_cuts
                adjusted_tokens = _adjust_tokens_for_cuts(tokens, keep_ranges, Cutter.SEGMENT_GAP)

                # Burn streaming captions
                captioner.burn_streaming_captions(
                    temp_cut,
                    adjusted_tokens,
                    Path(path),
                    max_words=self._config.max_caption_words
                )

                # Clean up temp file
                if temp_cut.exists():
                    temp_cut.unlink()
            else:
                # No tokens - just copy the cut video
                import shutil
                shutil.move(str(temp_cut), path)

            progress.setValue(100)
            QMessageBox.information(self, "Export Complete", f"Video exported to:\n{path}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Export failed: {e}")
        finally:
            progress.close()

    def closeEvent(self, event):
        """Handle window close."""
        if self._unsaved_changes:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Save before closing?",
                QMessageBox.StandardButton.Save |
                QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel
            )
            if reply == QMessageBox.StandardButton.Save:
                self._save_project()
                event.accept()
            elif reply == QMessageBox.StandardButton.Discard:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()
