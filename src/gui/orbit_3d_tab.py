from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg as FigureCanvas,
)
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d import proj3d
from mpl_toolkits.mplot3d.art3d import Line3DCollection
from PySide6.QtCore import (
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from sgp4.api import Satrec

from src.core.comparator import (
    OrbitRecord,
    compare_orbits,
)
from src.core.paths import (
    get_reference_dir,
    get_tle_dir,
)
from src.core.propagator import (
    EARTH_RADIUS_KM,
    datetime_to_julian,
)
from src.core.satellites import (
    get_satellite,
)
from src.io.eof_parser import (
    PreciseOrbitState,
    parse_eof_file,
)
from src.io.frame_converter import (
    teme_to_itrs,
)
from src.io.tle_reader import (
    TLEData,
    load_tle_catalog,
)


ERROR_THRESHOLDS_KM = (
    1.0,
    2.0,
    5.0,
    10.0,
)

# Presentation-only theme. None of these values affect orbit calculations.
SPACE_BG = "#050912"
PANEL_BG = "#091321"
GRID_COLOR = "#2E415C"
TEXT_COLOR = "#E2ECF8"
MUTED_TEXT = "#91A7C3"
EARTH_COLOR = "#174A78"
ATMOSPHERE_COLOR = "#67C4FF"
REFERENCE_COLOR = "#86D4FF"
PREDICTION_COLOR = "#FFB66E"
CONNECTOR_COLOR = "#EDF5FF"
CURSOR_COLOR = "#FFFFFF"


class Orbit3DTab(QWidget):
    """
    Interactive ORBIT-TRUST orbit visualization.

    Scientific data are always kept at their real numerical values.
    Optional error magnification changes only the displayed separation
    between the SGP4 trajectory and the precise reference trajectory.

    Mouse controls:
        Left-drag / normal Matplotlib 3D drag -> rotate
        Mouse wheel -> zoom in/out
    """

    def __init__(self) -> None:
        super().__init__()

        self.tle_entries: list[TLEData] = []

        # Parsed precise-orbit cache by satellite key.
        self.reference_cache: dict[
            str,
            list[PreciseOrbitState],
        ] = {}

        # Current rendered dataset.
        self.current_data: dict[
            str,
            Any,
        ] | None = None

        self.current_prediction_marker = None
        self.current_reference_marker = None
        self.current_connector = None

        # Always-visible 2D HUD markers. Matplotlib's mplot3d depth
        # sorting can incorrectly place ordinary 3D markers behind the
        # Earth surface. These markers are projected from the real 3D
        # coordinates onto the screen and drawn above the 3D scene.
        self.prediction_hud_marker = None
        self.reference_hud_marker = None
        self.current_reference_xyz = None
        self.current_displayed_prediction_xyz = None

        self.error_cursor = None
        self.colorbar = None

        self.animation_timer = QTimer(
            self
        )

        # Animation-only clock. This does not change the scientific
        # propagation interval or add observations to the experiment.
        self.animation_elapsed_seconds = 0.0

        # Default target: 30 FPS. The user can also test 15 or 60 FPS.
        self.animation_timer.setInterval(
            33
        )
        self.animation_timer.timeout.connect(
            self.advance_animation
        )

        self.build_interface()
        self.load_tle_choices()

    # -----------------------------------------------------------------
    # Interface
    # -----------------------------------------------------------------

    def build_interface(
        self,
    ) -> None:
        main_layout = QVBoxLayout(
            self
        )

        title = QLabel(
            "3D Orbit Visualization"
        )
        title.setStyleSheet(
            "font-size: 24px; "
            "font-weight: bold;"
        )

        description = QLabel(
            "Compare the TLE-SGP4 prediction with the Sentinel precise "
            "reference trajectory. Error vectors, visual magnification, "
            "thresholds, and a synchronized error graph make small "
            "kilometer-scale differences visible at Earth scale."
        )
        description.setWordWrap(
            True
        )

        controls_hint = QLabel(
            "Mouse: drag to rotate • scroll wheel to zoom • "
            "30 FPS recommended"
        )
        controls_hint.setStyleSheet(
            "font-size: 12px; "
            "font-weight: bold;"
        )

        main_layout.addWidget(
            title
        )
        main_layout.addWidget(
            description
        )
        main_layout.addWidget(
            controls_hint
        )

        splitter = QSplitter(
            Qt.Orientation.Horizontal
        )

        splitter.addWidget(
            self.create_control_panel()
        )
        splitter.addWidget(
            self.create_plot_panel()
        )

        splitter.setStretchFactor(
            0,
            0,
        )
        splitter.setStretchFactor(
            1,
            1,
        )
        splitter.setSizes(
            [
                345,
                900,
            ]
        )

        main_layout.addWidget(
            splitter,
            1,
        )

    def create_control_panel(
        self,
    ) -> QWidget:
        panel = QWidget()
        panel.setMaximumWidth(
            395
        )

        layout = QVBoxLayout(
            panel
        )

        # -------------------------------------------------------------
        # Orbit selection
        # -------------------------------------------------------------

        data_group = QGroupBox(
            "Orbit Selection"
        )
        form = QFormLayout(
            data_group
        )

        self.satellite_combo = QComboBox()
        self.satellite_combo.addItem(
            "Sentinel-1A",
            "sentinel1a",
        )
        self.satellite_combo.addItem(
            "Sentinel-1B",
            "sentinel1b",
        )
        self.satellite_combo.currentIndexChanged.connect(
            self.satellite_changed
        )

        self.tle_combo = QComboBox()

        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(
            0.5,
            24.0,
        )
        self.duration_spin.setDecimals(
            1
        )
        self.duration_spin.setSingleStep(
            0.5
        )
        self.duration_spin.setValue(
            6.0
        )
        self.duration_spin.setSuffix(
            " h"
        )

        self.sample_interval_spin = QSpinBox()
        self.sample_interval_spin.setRange(
            1,
            30,
        )
        self.sample_interval_spin.setValue(
            5
        )
        self.sample_interval_spin.setSuffix(
            " min"
        )

        form.addRow(
            "Satellite:",
            self.satellite_combo,
        )
        form.addRow(
            "Historical TLE:",
            self.tle_combo,
        )
        form.addRow(
            "Duration:",
            self.duration_spin,
        )
        form.addRow(
            "Render interval:",
            self.sample_interval_spin,
        )

        layout.addWidget(
            data_group
        )

        # -------------------------------------------------------------
        # Difference visualization
        # -------------------------------------------------------------

        difference_group = QGroupBox(
            "Difference Visualization"
        )
        difference_form = QFormLayout(
            difference_group
        )

        self.predicted_checkbox = QCheckBox(
            "Show TLE-SGP4 trajectory"
        )
        self.predicted_checkbox.setChecked(
            True
        )

        self.reference_checkbox = QCheckBox(
            "Show precise reference trajectory"
        )
        self.reference_checkbox.setChecked(
            True
        )

        self.error_vectors_checkbox = QCheckBox(
            "Show error-vector connectors"
        )
        self.error_vectors_checkbox.setChecked(
            True
        )

        self.color_error_checkbox = QCheckBox(
            "Color SGP4 path by error magnitude"
        )
        self.color_error_checkbox.setChecked(
            True
        )

        self.magnification_combo = QComboBox()
        for label, multiplier in (
            ("Actual scale (1×)", 1.0),
            ("10×", 10.0),
            ("50×", 50.0),
            ("100×", 100.0),
            ("250×", 250.0),
            ("500×", 500.0),
        ):
            self.magnification_combo.addItem(
                label,
                multiplier,
            )

        # Make the difference obvious by default while preserving
        # the actual numerical error values.
        self.magnification_combo.setCurrentIndex(
            3
        )

        self.vector_spacing_spin = QSpinBox()
        self.vector_spacing_spin.setRange(
            1,
            100,
        )
        self.vector_spacing_spin.setValue(
            6
        )
        self.vector_spacing_spin.setSuffix(
            " points"
        )

        difference_form.addRow(
            "",
            self.predicted_checkbox,
        )
        difference_form.addRow(
            "",
            self.reference_checkbox,
        )
        difference_form.addRow(
            "",
            self.error_vectors_checkbox,
        )
        difference_form.addRow(
            "",
            self.color_error_checkbox,
        )
        difference_form.addRow(
            "Error magnification:",
            self.magnification_combo,
        )
        difference_form.addRow(
            "Vector spacing:",
            self.vector_spacing_spin,
        )

        layout.addWidget(
            difference_group
        )

        # -------------------------------------------------------------
        # Scene options
        # -------------------------------------------------------------

        scene_group = QGroupBox(
            "Scene"
        )
        scene_layout = QVBoxLayout(
            scene_group
        )

        self.earth_checkbox = QCheckBox(
            "Show Earth"
        )
        self.earth_checkbox.setChecked(
            True
        )

        self.axes_checkbox = QCheckBox(
            "Show coordinate axes"
        )
        self.axes_checkbox.setChecked(
            True
        )

        scene_layout.addWidget(
            self.earth_checkbox
        )
        scene_layout.addWidget(
            self.axes_checkbox
        )

        layout.addWidget(
            scene_group
        )

        # Redraw-only controls do not need to recalculate SGP4.
        for control in (
            self.predicted_checkbox,
            self.reference_checkbox,
            self.error_vectors_checkbox,
            self.color_error_checkbox,
            self.earth_checkbox,
            self.axes_checkbox,
        ):
            control.toggled.connect(
                self.redraw_cached_scene
            )

        self.magnification_combo.currentIndexChanged.connect(
            self.redraw_cached_scene
        )
        self.vector_spacing_spin.valueChanged.connect(
            self.redraw_cached_scene
        )

        # -------------------------------------------------------------
        # Render / camera
        # -------------------------------------------------------------

        self.render_button = QPushButton(
            "Render 3D Orbit"
        )
        self.render_button.setMinimumHeight(
            44
        )
        self.render_button.clicked.connect(
            self.render_orbit
        )

        self.reset_view_button = QPushButton(
            "Reset Camera"
        )
        self.reset_view_button.clicked.connect(
            self.reset_camera
        )

        layout.addWidget(
            self.render_button
        )
        layout.addWidget(
            self.reset_view_button
        )

        # -------------------------------------------------------------
        # Time playback
        # -------------------------------------------------------------

        playback_group = QGroupBox(
            "Time Playback"
        )
        playback_layout = QVBoxLayout(
            playback_group
        )

        self.time_slider = QSlider(
            Qt.Orientation.Horizontal
        )
        self.time_slider.setRange(
            0,
            0,
        )
        self.time_slider.setEnabled(
            False
        )
        self.time_slider.valueChanged.connect(
            self.update_time_cursor
        )

        animation_form = QFormLayout()

        self.fps_combo = QComboBox()
        self.fps_combo.addItem(
            "15 FPS",
            15,
        )
        self.fps_combo.addItem(
            "30 FPS",
            30,
        )
        self.fps_combo.addItem(
            "60 FPS",
            60,
        )
        self.fps_combo.setCurrentIndex(
            1
        )
        self.fps_combo.currentIndexChanged.connect(
            self.update_animation_timer_rate
        )

        self.playback_rate_combo = QComboBox()
        self.playback_rate_combo.addItem(
            "600× real time",
            600.0,
        )
        self.playback_rate_combo.addItem(
            "1200× real time",
            1200.0,
        )
        self.playback_rate_combo.addItem(
            "2400× real time",
            2400.0,
        )
        self.playback_rate_combo.setCurrentIndex(
            1
        )

        self.smooth_motion_checkbox = QCheckBox(
            "Smooth visual interpolation"
        )
        self.smooth_motion_checkbox.setChecked(
            True
        )
        self.smooth_motion_checkbox.toggled.connect(
            self.update_time_cursor
        )

        animation_form.addRow(
            "Target refresh:",
            self.fps_combo,
        )
        animation_form.addRow(
            "Playback rate:",
            self.playback_rate_combo,
        )
        animation_form.addRow(
            "",
            self.smooth_motion_checkbox,
        )

        playback_buttons = QHBoxLayout()

        self.play_button = QPushButton(
            "Play"
        )
        self.play_button.setEnabled(
            False
        )
        self.play_button.clicked.connect(
            self.toggle_animation
        )

        self.restart_button = QPushButton(
            "Restart"
        )
        self.restart_button.setEnabled(
            False
        )
        self.restart_button.clicked.connect(
            self.restart_animation
        )

        playback_buttons.addWidget(
            self.play_button
        )
        playback_buttons.addWidget(
            self.restart_button
        )

        self.time_label = QLabel(
            "No rendered trajectory"
        )
        self.time_label.setWordWrap(
            True
        )

        playback_layout.addWidget(
            self.time_slider
        )
        playback_layout.addLayout(
            animation_form
        )
        playback_layout.addLayout(
            playback_buttons
        )
        playback_layout.addWidget(
            self.time_label
        )

        layout.addWidget(
            playback_group
        )

        # -------------------------------------------------------------
        # Current values
        # -------------------------------------------------------------

        info_group = QGroupBox(
            "Current Error"
        )
        info_layout = QVBoxLayout(
            info_group
        )

        self.info_label = QLabel(
            "Select a TLE and render the orbit."
        )
        self.info_label.setWordWrap(
            True
        )
        self.info_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        info_layout.addWidget(
            self.info_label
        )

        layout.addWidget(
            info_group
        )

        warning = QLabel(
            "Important: magnification affects only the displayed "
            "separation. All error values shown by ORBIT-TRUST remain "
            "the real calculated values in kilometers."
        )
        warning.setWordWrap(
            True
        )
        warning.setStyleSheet(
            "font-size: 11px; "
            "font-weight: bold;"
        )

        layout.addWidget(
            warning
        )
        layout.addStretch(
            1
        )

        return panel

    def create_plot_panel(
        self,
    ) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(
            panel
        )

        self.figure = Figure(
            figsize=(
                10,
                8,
            ),
            facecolor=SPACE_BG,
        )

        self.canvas = FigureCanvas(
            self.figure
        )

        # No Matplotlib toolbar is added. Rotation stays mouse-drag,
        # while zoom is handled directly by the scroll wheel.
        grid = self.figure.add_gridspec(
            3,
            1,
            height_ratios=(
                4.6,
                0.12,
                1.7,
            ),
        )

        self.axes3d = self.figure.add_subplot(
            grid[0, 0],
            projection="3d",
        )

        self.error_axes = self.figure.add_subplot(
            grid[2, 0]
        )

        self.canvas.mpl_connect(
            "scroll_event",
            self.on_scroll_zoom,
        )

        # Keep the always-visible HUD satellite markers aligned while
        # the user rotates or zooms the 3D camera.
        self.canvas.mpl_connect(
            "motion_notify_event",
            self.refresh_hud_markers,
        )
        self.canvas.mpl_connect(
            "button_release_event",
            self.refresh_hud_markers,
        )

        layout.addWidget(
            self.canvas,
            1,
        )

        self.draw_empty_scene()

        return panel

    # -----------------------------------------------------------------
    # Data discovery
    # -----------------------------------------------------------------

    def selected_satellite_key(
        self,
    ) -> str:
        return str(
            self.satellite_combo.currentData()
        )

    def satellite_changed(
        self,
        *_args,
    ) -> None:
        self.stop_animation()
        self.current_data = None
        self.load_tle_choices()
        self.draw_empty_scene()

    def find_best_tle_catalog(
        self,
        satellite_key: str,
    ) -> Path:
        tle_directory = get_tle_dir(
            satellite_key
        )

        sampled_files = sorted(
            tle_directory.glob(
                "*_sampled_*.tle"
            ),
            key=lambda path: (
                path.stat().st_mtime
            ),
            reverse=True,
        )

        if sampled_files:
            return sampled_files[0]

        raw_files = sorted(
            tle_directory.glob(
                "*.tle"
            ),
            key=lambda path: (
                path.stat().st_mtime
            ),
            reverse=True,
        )

        if raw_files:
            return raw_files[0]

        satellite = get_satellite(
            satellite_key
        )

        raise FileNotFoundError(
            "No historical TLE catalog was found for "
            f"{satellite.name} in:\n{tle_directory}"
        )

    def load_tle_choices(
        self,
        *_args,
    ) -> None:
        if not hasattr(
            self,
            "tle_combo",
        ):
            return

        self.tle_combo.clear()

        satellite_key = (
            self.selected_satellite_key()
        )

        try:
            catalog = (
                self.find_best_tle_catalog(
                    satellite_key
                )
            )

            self.tle_entries = (
                load_tle_catalog(
                    catalog
                )
            )

            for index, tle in enumerate(
                self.tle_entries
            ):
                label = (
                    f"{tle.epoch.date().isoformat()}  "
                    f"{tle.epoch.time().replace(microsecond=0).isoformat()}"
                )

                self.tle_combo.addItem(
                    label,
                    index,
                )

            self.tle_combo.setToolTip(
                str(
                    catalog
                )
            )

        except Exception as error:
            self.tle_entries = []

            self.tle_combo.addItem(
                "No TLE data available"
            )

            self.tle_combo.setToolTip(
                str(
                    error
                )
            )

    # -----------------------------------------------------------------
    # Reference data
    # -----------------------------------------------------------------

    @staticmethod
    def eof_filename_interval(
        path: Path,
    ) -> tuple[
        datetime,
        datetime,
    ] | None:
        """
        Extract Sentinel validity interval from names containing:
        _VYYYYMMDDTHHMMSS_YYYYMMDDTHHMMSS
        """

        match = re.search(
            r"_V(\d{8}T\d{6})_(\d{8}T\d{6})",
            path.name,
        )

        if match is None:
            return None

        start = datetime.strptime(
            match.group(
                1
            ),
            "%Y%m%dT%H%M%S",
        ).replace(
            tzinfo=(
                __import__(
                    "datetime"
                ).timezone.utc
            )
        )

        end = datetime.strptime(
            match.group(
                2
            ),
            "%Y%m%dT%H%M%S",
        ).replace(
            tzinfo=(
                __import__(
                    "datetime"
                ).timezone.utc
            )
        )

        return (
            start,
            end,
        )

    def get_candidate_eof_files(
        self,
        satellite_key: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Path]:
        reference_directory = (
            get_reference_dir(
                satellite_key
            )
        )

        all_files = sorted(
            reference_directory.glob(
                "*.EOF"
            )
        )

        if not all_files:
            satellite = get_satellite(
                satellite_key
            )

            raise FileNotFoundError(
                "No precise-orbit EOF files were found for "
                f"{satellite.name} in:\n"
                f"{reference_directory}"
            )

        candidates: list[
            Path
        ] = []

        for eof_file in all_files:
            interval = (
                self.eof_filename_interval(
                    eof_file
                )
            )

            # If the filename cannot be interpreted, keep the file
            # rather than risk excluding relevant reference data.
            if interval is None:
                candidates.append(
                    eof_file
                )
                continue

            validity_start, validity_end = interval

            if (
                validity_end >= start_time
                and validity_start <= end_time
            ):
                candidates.append(
                    eof_file
                )

        if not candidates:
            candidates = all_files

        return candidates

    def load_reference_states(
        self,
        satellite_key: str,
        tle: TLEData,
        duration_hours: float,
        interval_minutes: int,
    ) -> list[PreciseOrbitState]:
        start_time = tle.epoch
        end_time = (
            tle.epoch
            + timedelta(
                hours=duration_hours
            )
        )

        # Cache the whole available reference trajectory after the first
        # visualization for a satellite.
        if satellite_key not in self.reference_cache:
            states_by_time: dict[
                datetime,
                PreciseOrbitState,
            ] = {}

            eof_files = (
                self.get_candidate_eof_files(
                    satellite_key=satellite_key,
                    start_time=start_time,
                    end_time=end_time,
                )
            )

            for eof_file in eof_files:
                for state in parse_eof_file(
                    eof_file
                ):
                    states_by_time[
                        state.time
                    ] = state

            self.reference_cache[
                satellite_key
            ] = sorted(
                states_by_time.values(),
                key=lambda state: state.time,
            )

        all_states = (
            self.reference_cache[
                satellite_key
            ]
        )

        selected: list[
            PreciseOrbitState
        ] = []

        minimum_seconds = (
            interval_minutes
            * 60
        )

        last_time = None

        for state in all_states:
            if state.time < start_time:
                continue

            if state.time > end_time:
                break

            if last_time is None:
                selected.append(
                    state
                )
                last_time = state.time
                continue

            elapsed = (
                state.time
                - last_time
            ).total_seconds()

            if (
                elapsed
                >= minimum_seconds
            ):
                selected.append(
                    state
                )
                last_time = state.time

        if len(
            selected
        ) < 2:
            # Cache may have been built for a different date window.
            # Reload specifically around this TLE once.
            self.reference_cache.pop(
                satellite_key,
                None,
            )

            states_by_time: dict[
                datetime,
                PreciseOrbitState,
            ] = {}

            for eof_file in self.get_candidate_eof_files(
                satellite_key=satellite_key,
                start_time=start_time,
                end_time=end_time,
            ):
                for state in parse_eof_file(
                    eof_file
                ):
                    states_by_time[
                        state.time
                    ] = state

            self.reference_cache[
                satellite_key
            ] = sorted(
                states_by_time.values(),
                key=lambda state: state.time,
            )

            return self.load_reference_states(
                satellite_key=satellite_key,
                tle=tle,
                duration_hours=duration_hours,
                interval_minutes=interval_minutes,
            )

        return selected

    # -----------------------------------------------------------------
    # Propagation / matching
    # -----------------------------------------------------------------

    def build_predicted_records(
        self,
        tle: TLEData,
        reference_states: list[
            PreciseOrbitState
        ],
    ) -> list[OrbitRecord]:
        satellite = Satrec.twoline2rv(
            tle.line_1,
            tle.line_2,
        )

        records: list[
            OrbitRecord
        ] = []

        for reference in reference_states:
            jd, fraction = (
                datetime_to_julian(
                    reference.time
                )
            )

            error_code, position, velocity = (
                satellite.sgp4(
                    jd,
                    fraction,
                )
            )

            if error_code != 0:
                continue

            converted = teme_to_itrs(
                time=reference.time,
                position_km=(
                    position[0],
                    position[1],
                    position[2],
                ),
                velocity_km_s=(
                    velocity[0],
                    velocity[1],
                    velocity[2],
                ),
            )

            records.append(
                OrbitRecord(
                    time=converted.time,
                    x_km=converted.x_km,
                    y_km=converted.y_km,
                    z_km=converted.z_km,
                    vx_km_s=converted.vx_km_s,
                    vy_km_s=converted.vy_km_s,
                    vz_km_s=converted.vz_km_s,
                )
            )

        if len(
            records
        ) < 2:
            raise RuntimeError(
                "SGP4 produced too few valid states "
                "for the selected visualization."
            )

        return records

    @staticmethod
    def build_reference_records(
        states: list[
            PreciseOrbitState
        ],
    ) -> list[OrbitRecord]:
        return [
            OrbitRecord(
                time=state.time,
                x_km=state.x_km,
                y_km=state.y_km,
                z_km=state.z_km,
                vx_km_s=state.vx_km_s,
                vy_km_s=state.vy_km_s,
                vz_km_s=state.vz_km_s,
            )
            for state in states
        ]

    @staticmethod
    def align_records(
        predicted_records: list[
            OrbitRecord
        ],
        reference_records: list[
            OrbitRecord
        ],
    ) -> tuple[
        list[OrbitRecord],
        list[OrbitRecord],
    ]:
        reference_by_time = {
            round(
                record.time.timestamp()
            ): record
            for record in reference_records
        }

        matched_predicted: list[
            OrbitRecord
        ] = []
        matched_reference: list[
            OrbitRecord
        ] = []

        for predicted in predicted_records:
            reference = reference_by_time.get(
                round(
                    predicted.time.timestamp()
                )
            )

            if reference is None:
                continue

            if abs(
                predicted.time.timestamp()
                - reference.time.timestamp()
            ) > 1.0:
                continue

            matched_predicted.append(
                predicted
            )
            matched_reference.append(
                reference
            )

        if len(
            matched_predicted
        ) < 2:
            raise ValueError(
                "Too few matching SGP4/reference timestamps "
                "were available for visualization."
            )

        return (
            matched_predicted,
            matched_reference,
        )

    # -----------------------------------------------------------------
    # Main render
    # -----------------------------------------------------------------

    def render_orbit(
        self,
    ) -> None:
        if not self.tle_entries:
            QMessageBox.warning(
                self,
                "No TLE Data",
                "No historical TLE data are available "
                "for the selected satellite.",
            )
            return

        index = self.tle_combo.currentData()

        if index is None:
            return

        self.stop_animation()

        try:
            tle = self.tle_entries[
                int(
                    index
                )
            ]

            satellite_key = (
                self.selected_satellite_key()
            )
            satellite = get_satellite(
                satellite_key
            )

            duration_hours = float(
                self.duration_spin.value()
            )
            interval_minutes = int(
                self.sample_interval_spin.value()
            )

            self.render_button.setEnabled(
                False
            )
            self.render_button.setText(
                "Rendering..."
            )

            reference_states = (
                self.load_reference_states(
                    satellite_key=satellite_key,
                    tle=tle,
                    duration_hours=duration_hours,
                    interval_minutes=interval_minutes,
                )
            )

            predicted_records = (
                self.build_predicted_records(
                    tle=tle,
                    reference_states=reference_states,
                )
            )

            reference_records = (
                self.build_reference_records(
                    reference_states
                )
            )

            (
                predicted_records,
                reference_records,
            ) = self.align_records(
                predicted_records,
                reference_records,
            )

            comparison = compare_orbits(
                predicted_records=predicted_records,
                reference_records=reference_records,
            )

            errors = np.array(
                [
                    error.total_error_km
                    for error in comparison.errors
                ],
                dtype=float,
            )

            elapsed_hours = np.array(
                [
                    (
                        record.time
                        - tle.epoch
                    ).total_seconds()
                    / 3600.0
                    for record in predicted_records
                ],
                dtype=float,
            )

            predicted_xyz = np.array(
                [
                    (
                        record.x_km,
                        record.y_km,
                        record.z_km,
                    )
                    for record in predicted_records
                ],
                dtype=float,
            )

            reference_xyz = np.array(
                [
                    (
                        record.x_km,
                        record.y_km,
                        record.z_km,
                    )
                    for record in reference_records
                ],
                dtype=float,
            )

            times = [
                record.time
                for record in predicted_records
            ]

            self.current_data = {
                "satellite_name": satellite.name,
                "tle": tle,
                "predicted_records": predicted_records,
                "reference_records": reference_records,
                "predicted_xyz": predicted_xyz,
                "reference_xyz": reference_xyz,
                "errors": errors,
                "elapsed_hours": elapsed_hours,
                "times": times,
                "mean_error_km": (
                    comparison.mean_error_km
                ),
                "maximum_error_km": (
                    comparison.maximum_error_km
                ),
                "rms_error_km": (
                    comparison.rms_error_km
                ),
            }

            # The playback slider is normalized so animation can move
            # smoothly between scientific samples.
            self.time_slider.setRange(
                0,
                10000,
            )
            self.time_slider.setValue(
                0
            )
            self.animation_elapsed_seconds = 0.0
            self.time_slider.setEnabled(
                True
            )
            self.play_button.setEnabled(
                True
            )
            self.restart_button.setEnabled(
                True
            )

            self.draw_current_scene()
            self.update_time_cursor(
                0
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "3D Visualization Error",
                str(
                    error
                ),
            )

        finally:
            self.render_button.setEnabled(
                True
            )
            self.render_button.setText(
                "Render 3D Orbit"
            )

    def redraw_cached_scene(
        self,
        *_args,
    ) -> None:
        if self.current_data is None:
            return

        self.draw_current_scene()
        self.update_time_cursor(
            self.time_slider.value()
        )

    # -----------------------------------------------------------------
    # Scene drawing
    # -----------------------------------------------------------------

    def draw_current_scene(
        self,
    ) -> None:
        if self.current_data is None:
            return

        data = self.current_data

        self.axes3d.clear()
        self.error_axes.clear()

        # Clearing the axes removes the old overlay artists too.
        self.prediction_hud_marker = None
        self.reference_hud_marker = None

        self.apply_visual_theme()

        if self.colorbar is not None:
            try:
                self.colorbar.remove()
            except Exception:
                pass
            self.colorbar = None

        if self.earth_checkbox.isChecked():
            self.draw_earth()

        reference_xyz = data[
            "reference_xyz"
        ]
        predicted_xyz = data[
            "predicted_xyz"
        ]
        errors = data[
            "errors"
        ]
        elapsed_hours = data[
            "elapsed_hours"
        ]

        multiplier = float(
            self.magnification_combo.currentData()
        )

        # Only the displayed offset is magnified.
        displayed_predicted_xyz = (
            reference_xyz
            + (
                predicted_xyz
                - reference_xyz
            )
            * multiplier
        )

        data[
            "displayed_predicted_xyz"
        ] = displayed_predicted_xyz

        all_positions: list[
            np.ndarray
        ] = []

        # -------------------------------------------------------------
        # Precise reference trajectory
        # -------------------------------------------------------------

        if self.reference_checkbox.isChecked():
            self.axes3d.plot(
                reference_xyz[
                    :,
                    0,
                ],
                reference_xyz[
                    :,
                    1,
                ],
                reference_xyz[
                    :,
                    2,
                ],
                linewidth=2.0,
                linestyle="--",
                color=REFERENCE_COLOR,
                alpha=0.96,
                label="Precise reference",
            )

            all_positions.append(
                reference_xyz
            )

        # -------------------------------------------------------------
        # SGP4 trajectory
        # -------------------------------------------------------------

        if self.predicted_checkbox.isChecked():
            if (
                self.color_error_checkbox.isChecked()
                and len(
                    displayed_predicted_xyz
                ) > 1
            ):
                segments = np.stack(
                    [
                        displayed_predicted_xyz[
                            :-1
                        ],
                        displayed_predicted_xyz[
                            1:
                        ],
                    ],
                    axis=1,
                )

                segment_errors = (
                    errors[
                        :-1
                    ]
                    + errors[
                        1:
                    ]
                ) / 2.0

                minimum = float(
                    np.nanmin(
                        segment_errors
                    )
                )
                maximum = float(
                    np.nanmax(
                        segment_errors
                    )
                )

                if maximum <= minimum:
                    maximum = (
                        minimum
                        + 1e-9
                    )

                normalizer = Normalize(
                    vmin=minimum,
                    vmax=maximum,
                )

                collection = Line3DCollection(
                    segments,
                    cmap="plasma",
                    norm=normalizer,
                    linewidth=2.5,
                )

                collection.set_array(
                    segment_errors
                )

                self.axes3d.add_collection3d(
                    collection
                )

                self.colorbar = (
                    self.figure.colorbar(
                        collection,
                        ax=self.axes3d,
                        fraction=0.028,
                        pad=0.02,
                    )
                )
                self.colorbar.set_label(
                    "Actual total position error (km)",
                    color=TEXT_COLOR,
                )
                self.colorbar.ax.tick_params(
                    colors=MUTED_TEXT
                )
                try:
                    self.colorbar.outline.set_edgecolor(
                        GRID_COLOR
                    )
                except Exception:
                    pass

                # Invisible helper only for the legend.
                self.axes3d.plot(
                    [],
                    [],
                    [],
                    linewidth=2.7,
                    color=PREDICTION_COLOR,
                    label="TLE-SGP4 prediction",
                )

            else:
                self.axes3d.plot(
                    displayed_predicted_xyz[
                        :,
                        0,
                    ],
                    displayed_predicted_xyz[
                        :,
                        1,
                    ],
                    displayed_predicted_xyz[
                        :,
                        2,
                    ],
                    linewidth=2.5,
                    color=PREDICTION_COLOR,
                    alpha=0.96,
                    label="TLE-SGP4 prediction",
                )

            all_positions.append(
                displayed_predicted_xyz
            )

        # -------------------------------------------------------------
        # Error-vector connectors
        # -------------------------------------------------------------

        if (
            self.error_vectors_checkbox.isChecked()
            and self.predicted_checkbox.isChecked()
            and self.reference_checkbox.isChecked()
        ):
            spacing = max(
                1,
                int(
                    self.vector_spacing_spin.value()
                ),
            )

            connector_segments = np.stack(
                [
                    reference_xyz[
                        ::spacing
                    ],
                    displayed_predicted_xyz[
                        ::spacing
                    ],
                ],
                axis=1,
            )

            connector_collection = Line3DCollection(
                connector_segments,
                linewidth=0.9,
                alpha=0.58,
                colors=CONNECTOR_COLOR,
            )

            self.axes3d.add_collection3d(
                connector_collection
            )

        if not all_positions:
            self.axes3d.text2D(
                0.5,
                0.5,
                "Enable at least one trajectory.",
                transform=self.axes3d.transAxes,
                ha="center",
                va="center",
            )

            all_positions = [
                reference_xyz
            ]

        combined_positions = np.concatenate(
            all_positions,
            axis=0,
        )

        limit_km = max(
            EARTH_RADIUS_KM
            * 1.08,
            float(
                np.max(
                    np.abs(
                        combined_positions
                    )
                )
            )
            * 1.08,
        )

        self.configure_axes(
            limit_km
        )

        satellite_name = data[
            "satellite_name"
        ]
        tle = data[
            "tle"
        ]

        magnification_text = (
            "actual scale"
            if multiplier == 1.0
            else (
                f"displayed error ×{multiplier:g}"
            )
        )

        self.axes3d.set_title(
            f"{satellite_name} — "
            f"{tle.epoch.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"RMS {data['rms_error_km']:.3f} km | "
            f"Mean {data['mean_error_km']:.3f} km | "
            f"Max {data['maximum_error_km']:.3f} km | "
            f"{magnification_text}"
        )
        self.axes3d.title.set_color(
            TEXT_COLOR
        )

        legend = self.axes3d.legend(
            loc="upper left",
            frameon=True,
        )
        legend.get_frame().set_facecolor(
            PANEL_BG
        )
        legend.get_frame().set_edgecolor(
            GRID_COLOR
        )
        for legend_text in legend.get_texts():
            legend_text.set_color(
                TEXT_COLOR
            )

        # -------------------------------------------------------------
        # Synchronized error graph
        # -------------------------------------------------------------

        self.error_axes.plot(
            elapsed_hours,
            errors,
            linewidth=1.9,
            color=PREDICTION_COLOR,
            label="Total position error",
        )

        for threshold in ERROR_THRESHOLDS_KM:
            self.error_axes.axhline(
                threshold,
                linewidth=0.8,
                linestyle="--",
                alpha=0.58,
                color=MUTED_TEXT,
                label=(
                    f"{threshold:g} km threshold"
                ),
            )

        self.error_axes.set_xlabel(
            "Elapsed propagation time (hours)"
        )
        self.error_axes.set_ylabel(
            "Error (km)"
        )
        self.error_axes.xaxis.label.set_color(
            TEXT_COLOR
        )
        self.error_axes.yaxis.label.set_color(
            TEXT_COLOR
        )
        self.error_axes.grid(
            True,
            alpha=0.24,
            color=GRID_COLOR,
        )

        if len(
            elapsed_hours
        ):
            self.error_axes.set_xlim(
                float(
                    elapsed_hours[
                        0
                    ]
                ),
                float(
                    elapsed_hours[
                        -1
                    ]
                ),
            )

        self.error_cursor = (
            self.error_axes.axvline(
                elapsed_hours[
                    0
                ],
                linewidth=1.2,
                color=CURSOR_COLOR,
            )
        )

        # Keep threshold labels useful without consuming too much room.
        handles, labels = (
            self.error_axes.get_legend_handles_labels()
        )

        error_legend = self.error_axes.legend(
            handles,
            labels,
            loc="upper left",
            fontsize=8,
            ncols=3,
            frameon=True,
        )
        error_legend.get_frame().set_facecolor(
            PANEL_BG
        )
        error_legend.get_frame().set_edgecolor(
            GRID_COLOR
        )
        for legend_text in error_legend.get_texts():
            legend_text.set_color(
                TEXT_COLOR
            )

        # -------------------------------------------------------------
        # Current markers
        # -------------------------------------------------------------

        first_reference = (
            reference_xyz[
                0
            ]
        )
        first_prediction = (
            displayed_predicted_xyz[
                0
            ]
        )

        (
            self.current_reference_marker,
        ) = self.axes3d.plot(
            [
                first_reference[
                    0
                ]
            ],
            [
                first_reference[
                    1
                ]
            ],
            [
                first_reference[
                    2
                ]
            ],
            marker="o",
            linestyle="None",
            markersize=10,
            markerfacecolor=REFERENCE_COLOR,
            markeredgecolor=SPACE_BG,
            markeredgewidth=1.0,
            visible=False,
            label="_nolegend_",
        )

        (
            self.current_prediction_marker,
        ) = self.axes3d.plot(
            [
                first_prediction[
                    0
                ]
            ],
            [
                first_prediction[
                    1
                ]
            ],
            [
                first_prediction[
                    2
                ]
            ],
            marker="o",
            linestyle="None",
            markersize=10,
            markerfacecolor=PREDICTION_COLOR,
            markeredgecolor=SPACE_BG,
            markeredgewidth=1.0,
            visible=False,
            label="_nolegend_",
        )

        (
            self.current_connector,
        ) = self.axes3d.plot(
            [
                first_reference[
                    0
                ],
                first_prediction[
                    0
                ],
            ],
            [
                first_reference[
                    1
                ],
                first_prediction[
                    1
                ],
            ],
            [
                first_reference[
                    2
                ],
                first_prediction[
                    2
                ],
            ],
            linewidth=1.5,
            color=CONNECTOR_COLOR,
            alpha=0.85,
            label="_nolegend_",
        )

        # Store the initial live positions for the 2D overlay markers.
        self.current_reference_xyz = (
            first_reference.copy()
        )
        self.current_displayed_prediction_xyz = (
            first_prediction.copy()
        )

        self.create_hud_markers()

        self.axes3d.view_init(
            elev=24,
            azim=42,
        )

        self.figure.subplots_adjust(
            left=0.07,
            right=0.93,
            top=0.92,
            bottom=0.08,
            hspace=0.28,
        )

        self.canvas.draw_idle()

    def draw_empty_scene(
        self,
    ) -> None:
        self.axes3d.clear()
        self.error_axes.clear()

        # Clearing the axes removes the old overlay artists too.
        self.prediction_hud_marker = None
        self.reference_hud_marker = None

        self.apply_visual_theme()

        if self.colorbar is not None:
            try:
                self.colorbar.remove()
            except Exception:
                pass
            self.colorbar = None

        self.draw_earth()
        self.configure_axes(
            7600.0
        )

        self.axes3d.set_title(
            "Select a TLE and render the orbit"
        )
        self.axes3d.title.set_color(
            TEXT_COLOR
        )

        self.error_axes.set_xlabel(
            "Elapsed propagation time (hours)"
        )
        self.error_axes.set_ylabel(
            "Error (km)"
        )
        self.error_axes.xaxis.label.set_color(
            TEXT_COLOR
        )
        self.error_axes.yaxis.label.set_color(
            TEXT_COLOR
        )
        self.error_axes.grid(
            True,
            alpha=0.24,
            color=GRID_COLOR,
        )

        self.figure.subplots_adjust(
            left=0.07,
            right=0.93,
            top=0.92,
            bottom=0.08,
            hspace=0.28,
        )

        self.canvas.draw_idle()

    # -----------------------------------------------------------------
    # Earth / axes
    # -----------------------------------------------------------------

    def apply_visual_theme(
        self,
    ) -> None:
        """
        Apply the dark mission-control presentation style.

        This affects appearance only.
        """

        self.figure.set_facecolor(
            SPACE_BG
        )

        self.axes3d.set_facecolor(
            SPACE_BG
        )

        self.error_axes.set_facecolor(
            PANEL_BG
        )

        self.axes3d.tick_params(
            colors=MUTED_TEXT
        )

        self.error_axes.tick_params(
            colors=MUTED_TEXT
        )

        self.axes3d.xaxis.label.set_color(
            TEXT_COLOR
        )
        self.axes3d.yaxis.label.set_color(
            TEXT_COLOR
        )
        self.axes3d.zaxis.label.set_color(
            TEXT_COLOR
        )

        self.error_axes.xaxis.label.set_color(
            TEXT_COLOR
        )
        self.error_axes.yaxis.label.set_color(
            TEXT_COLOR
        )

        self.axes3d.title.set_color(
            TEXT_COLOR
        )
        self.error_axes.title.set_color(
            TEXT_COLOR
        )

        for spine in self.error_axes.spines.values():
            spine.set_color(
                GRID_COLOR
            )

        try:
            pane_color = (
                0.02,
                0.04,
                0.08,
                1.0,
            )

            self.axes3d.xaxis.set_pane_color(
                pane_color
            )
            self.axes3d.yaxis.set_pane_color(
                pane_color
            )
            self.axes3d.zaxis.set_pane_color(
                pane_color
            )

            for axis in (
                self.axes3d.xaxis,
                self.axes3d.yaxis,
                self.axes3d.zaxis,
            ):
                axis._axinfo[
                    "grid"
                ][
                    "color"
                ] = GRID_COLOR
        except Exception:
            pass

    def draw_earth(
        self,
    ) -> None:
        """
        Draw a lightweight stylized Earth.

        The globe is intentionally presentation-only: it adds no external
        texture or map dependency and therefore remains easy to package.
        """

        u = np.linspace(
            0,
            2
            * np.pi,
            96,
        )
        v = np.linspace(
            0,
            np.pi,
            48,
        )

        x = (
            EARTH_RADIUS_KM
            * np.outer(
                np.cos(
                    u
                ),
                np.sin(
                    v
                ),
            )
        )
        y = (
            EARTH_RADIUS_KM
            * np.outer(
                np.sin(
                    u
                ),
                np.sin(
                    v
                ),
            )
        )
        z = (
            EARTH_RADIUS_KM
            * np.outer(
                np.ones(
                    np.size(
                        u
                    )
                ),
                np.cos(
                    v
                ),
            )
        )

        self.axes3d.plot_surface(
            x,
            y,
            z,
            color=EARTH_COLOR,
            alpha=0.94,
            linewidth=0,
            antialiased=True,
            shade=True,
            zorder=1,
        )

        # A faint shell suggests atmosphere without requiring textures.
        atmosphere_radius = (
            EARTH_RADIUS_KM
            * 1.012
        )

        atmosphere_x = (
            atmosphere_radius
            * np.outer(
                np.cos(
                    u[
                        ::2
                    ]
                ),
                np.sin(
                    v[
                        ::2
                    ]
                ),
            )
        )
        atmosphere_y = (
            atmosphere_radius
            * np.outer(
                np.sin(
                    u[
                        ::2
                    ]
                ),
                np.sin(
                    v[
                        ::2
                    ]
                ),
            )
        )
        atmosphere_z = (
            atmosphere_radius
            * np.outer(
                np.ones(
                    np.size(
                        u[
                            ::2
                        ]
                    )
                ),
                np.cos(
                    v[
                        ::2
                    ]
                ),
            )
        )

        self.axes3d.plot_wireframe(
            atmosphere_x,
            atmosphere_y,
            atmosphere_z,
            rstride=20,
            cstride=20,
            color=ATMOSPHERE_COLOR,
            linewidth=0.45,
            alpha=0.16,
            zorder=2,
        )

        # Latitude guides.
        theta = np.linspace(
            0,
            2
            * np.pi,
            240,
        )

        for latitude_deg in (
            -60,
            -30,
            0,
            30,
            60,
        ):
            latitude = np.deg2rad(
                latitude_deg
            )

            radius_at_latitude = (
                EARTH_RADIUS_KM
                * np.cos(
                    latitude
                )
            )

            z_value = (
                EARTH_RADIUS_KM
                * np.sin(
                    latitude
                )
            )

            self.axes3d.plot(
                radius_at_latitude
                * np.cos(
                    theta
                ),
                radius_at_latitude
                * np.sin(
                    theta
                ),
                np.full_like(
                    theta,
                    z_value,
                ),
                color=ATMOSPHERE_COLOR,
                linewidth=(
                    0.8
                    if latitude_deg == 0
                    else 0.32
                ),
                alpha=(
                    0.50
                    if latitude_deg == 0
                    else 0.20
                ),
                zorder=3,
            )

        # Longitude guides.
        latitude_curve = np.linspace(
            -np.pi
            / 2,
            np.pi
            / 2,
            160,
        )

        for longitude_deg in range(
            0,
            360,
            30,
        ):
            longitude = np.deg2rad(
                longitude_deg
            )

            guide_x = (
                EARTH_RADIUS_KM
                * np.cos(
                    latitude_curve
                )
                * np.cos(
                    longitude
                )
            )

            guide_y = (
                EARTH_RADIUS_KM
                * np.cos(
                    latitude_curve
                )
                * np.sin(
                    longitude
                )
            )

            guide_z = (
                EARTH_RADIUS_KM
                * np.sin(
                    latitude_curve
                )
            )

            self.axes3d.plot(
                guide_x,
                guide_y,
                guide_z,
                color=ATMOSPHERE_COLOR,
                linewidth=0.28,
                alpha=0.17,
                zorder=3,
            )

    def configure_axes(
        self,
        limit_km: float,
    ) -> None:
        self.axes3d.set_xlim(
            -limit_km,
            limit_km,
        )
        self.axes3d.set_ylim(
            -limit_km,
            limit_km,
        )
        self.axes3d.set_zlim(
            -limit_km,
            limit_km,
        )

        try:
            self.axes3d.set_box_aspect(
                (
                    1,
                    1,
                    1,
                )
            )
        except AttributeError:
            pass

        if self.axes_checkbox.isChecked():
            self.axes3d.set_xlabel(
                "ITRS X (km)"
            )
            self.axes3d.set_ylabel(
                "ITRS Y (km)"
            )
            self.axes3d.set_zlabel(
                "ITRS Z (km)"
            )
            self.axes3d.grid(
                True
            )
            self.axes3d.xaxis.label.set_color(
                TEXT_COLOR
            )
            self.axes3d.yaxis.label.set_color(
                TEXT_COLOR
            )
            self.axes3d.zaxis.label.set_color(
                TEXT_COLOR
            )
        else:
            self.axes3d.set_axis_off()

    # -----------------------------------------------------------------
    # Always-visible satellite HUD markers
    # -----------------------------------------------------------------

    def create_hud_markers(
        self,
    ) -> None:
        """
        Create always-visible 2D overlay markers.

        Important:
        proj3d returns projected coordinates in Matplotlib's 2D data
        projection space, not in the 3D orbit's kilometer coordinates.
        We therefore convert those projected points into AXES-FRACTION
        coordinates before drawing the HUD markers.
        """

        if self.reference_hud_marker is not None:
            try:
                self.reference_hud_marker.remove()
            except Exception:
                pass

        if self.prediction_hud_marker is not None:
            try:
                self.prediction_hud_marker.remove()
            except Exception:
                pass

        # Precise reference = cyan ring.
        self.reference_hud_marker = self.axes3d.text2D(
            0.5,
            0.5,
            "○",
            transform=self.axes3d.transAxes,
            ha="center",
            va="center",
            fontsize=20,
            color=REFERENCE_COLOR,
            zorder=10000,
            clip_on=False,
        )

        # SGP4 prediction = solid orange satellite marker.
        self.prediction_hud_marker = self.axes3d.text2D(
            0.5,
            0.5,
            "●",
            transform=self.axes3d.transAxes,
            ha="center",
            va="center",
            fontsize=18,
            color=PREDICTION_COLOR,
            zorder=10001,
            clip_on=False,
        )

        self.refresh_hud_markers()

    def project_xyz_to_axes_fraction(
        self,
        xyz: np.ndarray,
    ) -> tuple[
        float,
        float,
    ]:
        """
        Project a real 3D orbit coordinate into a 0..1 axes position.

        This is the key step that prevents the marker from getting
        stuck near the center of the Earth.
        """

        projected_x, projected_y, _ = (
            proj3d.proj_transform(
                float(
                    xyz[
                        0
                    ]
                ),
                float(
                    xyz[
                        1
                    ]
                ),
                float(
                    xyz[
                        2
                    ]
                ),
                self.axes3d.get_proj(),
            )
        )

        # proj3d -> Matplotlib 2D data coordinates -> display pixels
        display_xy = self.axes3d.transData.transform(
            (
                projected_x,
                projected_y,
            )
        )

        # Display pixels -> normalized Axes coordinates.
        axes_x, axes_y = (
            self.axes3d.transAxes.inverted().transform(
                display_xy
            )
        )

        return (
            float(
                axes_x
            ),
            float(
                axes_y
            ),
        )

    def refresh_hud_markers(
        self,
        *_args,
    ) -> None:
        """
        Reproject the two live satellite markers after animation,
        scroll-wheel zoom, or camera rotation.
        """

        if self.current_data is None:
            return

        if (
            self.current_reference_xyz is None
            or self.current_displayed_prediction_xyz is None
        ):
            return

        if self.reference_hud_marker is not None:
            reference_xy = (
                self.project_xyz_to_axes_fraction(
                    self.current_reference_xyz
                )
            )

            self.reference_hud_marker.set_position(
                reference_xy
            )

            self.reference_hud_marker.set_visible(
                self.reference_checkbox.isChecked()
            )

        if self.prediction_hud_marker is not None:
            prediction_xy = (
                self.project_xyz_to_axes_fraction(
                    self.current_displayed_prediction_xyz
                )
            )

            self.prediction_hud_marker.set_position(
                prediction_xy
            )

            self.prediction_hud_marker.set_visible(
                self.predicted_checkbox.isChecked()
            )

    # -----------------------------------------------------------------
    # Scroll-wheel zoom
    # -----------------------------------------------------------------

    def on_scroll_zoom(
        self,
        event,
    ) -> None:
        """
        Zoom the 3D scene around its current center.

        Scroll up   -> zoom in
        Scroll down -> zoom out
        """

        if event.inaxes is not self.axes3d:
            return

        if event.button == "up":
            scale_factor = 0.84
        elif event.button == "down":
            scale_factor = 1.19
        else:
            return

        for getter, setter in (
            (
                self.axes3d.get_xlim3d,
                self.axes3d.set_xlim3d,
            ),
            (
                self.axes3d.get_ylim3d,
                self.axes3d.set_ylim3d,
            ),
            (
                self.axes3d.get_zlim3d,
                self.axes3d.set_zlim3d,
            ),
        ):
            lower, upper = getter()

            center = (
                lower
                + upper
            ) / 2.0

            half_range = (
                upper
                - lower
            ) / 2.0

            new_half_range = (
                half_range
                * scale_factor
            )

            setter(
                center
                - new_half_range,
                center
                + new_half_range,
            )

        self.refresh_hud_markers()
        self.canvas.draw_idle()

    def reset_camera(
        self,
    ) -> None:
        self.axes3d.view_init(
            elev=24,
            azim=42,
        )

        if self.current_data is not None:
            self.draw_current_scene()

        self.canvas.draw_idle()

    # -----------------------------------------------------------------
    # Time cursor / animation
    # -----------------------------------------------------------------

    def update_animation_timer_rate(
        self,
        *_args,
    ) -> None:
        """
        Change only the visual refresh target.

        Scientific TLE/POEORB samples and statistical calculations are
        completely unaffected by this setting.
        """

        if not hasattr(
            self,
            "fps_combo",
        ):
            return

        fps = int(
            self.fps_combo.currentData()
            or 30
        )

        interval_ms = max(
            1,
            round(
                1000
                / fps
            ),
        )

        self.animation_timer.setInterval(
            interval_ms
        )

    def interpolation_state(
        self,
        target_elapsed_seconds: float,
    ) -> tuple[
        np.ndarray,
        np.ndarray,
        np.ndarray,
        float,
        datetime,
        bool,
        int,
    ]:
        """
        Return the displayed state at a continuous animation time.

        When smooth interpolation is enabled, positions and total error are
        linearly interpolated only for presentation. No interpolated point is
        added to the research dataset.
        """

        if self.current_data is None:
            raise RuntimeError(
                "No orbit is currently rendered."
            )

        data = self.current_data

        sample_seconds = (
            data[
                "elapsed_hours"
            ]
            * 3600.0
        )

        target = float(
            np.clip(
                target_elapsed_seconds,
                float(
                    sample_seconds[
                        0
                    ]
                ),
                float(
                    sample_seconds[
                        -1
                    ]
                ),
            )
        )

        smooth = (
            self.smooth_motion_checkbox.isChecked()
        )

        if not smooth:
            nearest_index = int(
                np.argmin(
                    np.abs(
                        sample_seconds
                        - target
                    )
                )
            )

            reference = data[
                "reference_xyz"
            ][
                nearest_index
            ]

            actual_prediction = data[
                "predicted_xyz"
            ][
                nearest_index
            ]

            displayed_prediction = data[
                "displayed_predicted_xyz"
            ][
                nearest_index
            ]

            error = float(
                data[
                    "errors"
                ][
                    nearest_index
                ]
            )

            time = data[
                "times"
            ][
                nearest_index
            ]

            return (
                reference,
                actual_prediction,
                displayed_prediction,
                error,
                time,
                True,
                nearest_index,
            )

        upper_index = int(
            np.searchsorted(
                sample_seconds,
                target,
                side="left",
            )
        )

        if upper_index <= 0:
            lower_index = 0
            upper_index = 0
            fraction = 0.0
        elif upper_index >= len(
            sample_seconds
        ):
            lower_index = (
                len(
                    sample_seconds
                )
                - 1
            )
            upper_index = lower_index
            fraction = 0.0
        else:
            lower_index = (
                upper_index
                - 1
            )

            lower_time = float(
                sample_seconds[
                    lower_index
                ]
            )
            upper_time = float(
                sample_seconds[
                    upper_index
                ]
            )

            span = (
                upper_time
                - lower_time
            )

            if span <= 0:
                fraction = 0.0
            else:
                fraction = (
                    target
                    - lower_time
                ) / span

        if (
            lower_index
            == upper_index
        ):
            fraction = 0.0

        reference = (
            data[
                "reference_xyz"
            ][
                lower_index
            ]
            * (
                1.0
                - fraction
            )
            + data[
                "reference_xyz"
            ][
                upper_index
            ]
            * fraction
        )

        actual_prediction = (
            data[
                "predicted_xyz"
            ][
                lower_index
            ]
            * (
                1.0
                - fraction
            )
            + data[
                "predicted_xyz"
            ][
                upper_index
            ]
            * fraction
        )

        displayed_prediction = (
            data[
                "displayed_predicted_xyz"
            ][
                lower_index
            ]
            * (
                1.0
                - fraction
            )
            + data[
                "displayed_predicted_xyz"
            ][
                upper_index
            ]
            * fraction
        )

        error = float(
            data[
                "errors"
            ][
                lower_index
            ]
            * (
                1.0
                - fraction
            )
            + data[
                "errors"
            ][
                upper_index
            ]
            * fraction
        )

        tle = data[
            "tle"
        ]

        time = (
            tle.epoch
            + timedelta(
                seconds=target
            )
        )

        exactly_sampled = bool(
            lower_index
            == upper_index
            or fraction <= 1e-9
            or fraction >= (
                1.0
                - 1e-9
            )
        )

        nearest_index = (
            lower_index
            if fraction < 0.5
            else upper_index
        )

        return (
            reference,
            actual_prediction,
            displayed_prediction,
            error,
            time,
            exactly_sampled,
            nearest_index,
        )

    def update_time_cursor(
        self,
        slider_value: int | bool | None = None,
    ) -> None:
        if self.current_data is None:
            return

        data = self.current_data

        # Checkbox signals can pass bool; in that case use the existing
        # slider position rather than treating True/False as a time.
        if isinstance(
            slider_value,
            bool,
        ) or slider_value is None:
            slider_value = (
                self.time_slider.value()
            )

        slider_maximum = max(
            1,
            self.time_slider.maximum(),
        )

        progress = (
            float(
                slider_value
            )
            / slider_maximum
        )

        total_elapsed_seconds = float(
            data[
                "elapsed_hours"
            ][
                -1
            ]
            * 3600.0
        )

        target_elapsed_seconds = (
            progress
            * total_elapsed_seconds
        )

        self.animation_elapsed_seconds = (
            target_elapsed_seconds
        )

        (
            reference,
            actual_prediction,
            displayed_prediction,
            error,
            time,
            exactly_sampled,
            nearest_index,
        ) = self.interpolation_state(
            target_elapsed_seconds
        )

        elapsed = (
            target_elapsed_seconds
            / 3600.0
        )

        self.current_reference_xyz = (
            np.array(
                reference,
                dtype=float,
            )
        )

        self.current_displayed_prediction_xyz = (
            np.array(
                displayed_prediction,
                dtype=float,
            )
        )

        if self.current_reference_marker is not None:
            self.current_reference_marker.set_data_3d(
                [
                    reference[
                        0
                    ]
                ],
                [
                    reference[
                        1
                    ]
                ],
                [
                    reference[
                        2
                    ]
                ],
            )

        if self.current_prediction_marker is not None:
            self.current_prediction_marker.set_data_3d(
                [
                    displayed_prediction[
                        0
                    ]
                ],
                [
                    displayed_prediction[
                        1
                    ]
                ],
                [
                    displayed_prediction[
                        2
                    ]
                ],
            )

        if self.current_connector is not None:
            self.current_connector.set_data_3d(
                [
                    reference[
                        0
                    ],
                    displayed_prediction[
                        0
                    ],
                ],
                [
                    reference[
                        1
                    ],
                    displayed_prediction[
                        1
                    ],
                ],
                [
                    reference[
                        2
                    ],
                    displayed_prediction[
                        2
                    ],
                ],
            )

        if self.error_cursor is not None:
            self.error_cursor.set_xdata(
                [
                    elapsed,
                    elapsed,
                ]
            )

        multiplier = float(
            self.magnification_combo.currentData()
        )

        if error < 1.0:
            reliability_status = (
                "Below 1 km threshold"
            )
        elif error < 2.0:
            reliability_status = (
                "1 km exceeded; below 2 km"
            )
        elif error < 5.0:
            reliability_status = (
                "2 km exceeded; below 5 km"
            )
        elif error < 10.0:
            reliability_status = (
                "5 km exceeded; below 10 km"
            )
        else:
            reliability_status = (
                "10 km threshold exceeded"
            )

        fps = int(
            self.fps_combo.currentData()
            or 30
        )

        rate = float(
            self.playback_rate_combo.currentData()
            or 1200.0
        )

        self.time_label.setText(
            f"Elapsed: {elapsed:.3f} h • "
            f"UTC: {time.isoformat()} • "
            f"Target: {fps} FPS • "
            f"Playback: {rate:g}×"
        )

        if (
            self.smooth_motion_checkbox.isChecked()
            and not exactly_sampled
        ):
            error_label = (
                "Interpolated visual error"
            )
            sample_note = (
                "Visual interpolation only; "
                f"nearest scientific sample #{nearest_index + 1}"
            )
        else:
            error_label = (
                "Sampled total error"
            )
            sample_note = (
                f"Scientific sample #{nearest_index + 1}"
            )

        self.info_label.setText(
            f"<b>{error_label}:</b> {error:.3f} km<br>"
            f"<b>Threshold status:</b> {reliability_status}<br>"
            f"<b>Display magnification:</b> {multiplier:g}×<br>"
            f"<b>Animation:</b> {sample_note}<br>"
            f"<b>SGP4 position:</b> "
            f"({actual_prediction[0]:.1f}, "
            f"{actual_prediction[1]:.1f}, "
            f"{actual_prediction[2]:.1f}) km<br>"
            f"<b>Precise reference:</b> "
            f"({reference[0]:.1f}, "
            f"{reference[1]:.1f}, "
            f"{reference[2]:.1f}) km"
        )

        # Reproject the always-visible 2D markers from the current
        # real 3D positions before scheduling the frame redraw.
        self.refresh_hud_markers()

        # draw_idle coalesces repeated paint requests, which is preferable
        # to forcing a complete synchronous redraw every timer tick.
        self.canvas.draw_idle()

    def toggle_animation(
        self,
    ) -> None:
        if self.animation_timer.isActive():
            self.stop_animation()
            return

        if self.current_data is None:
            return

        if (
            self.time_slider.value()
            >= self.time_slider.maximum()
        ):
            self.time_slider.setValue(
                0
            )

        data = self.current_data

        total_elapsed_seconds = float(
            data[
                "elapsed_hours"
            ][
                -1
            ]
            * 3600.0
        )

        slider_maximum = max(
            1,
            self.time_slider.maximum(),
        )

        self.animation_elapsed_seconds = (
            (
                self.time_slider.value()
                / slider_maximum
            )
            * total_elapsed_seconds
        )

        self.update_animation_timer_rate()
        self.animation_timer.start()

        self.play_button.setText(
            "Pause"
        )

    def stop_animation(
        self,
    ) -> None:
        self.animation_timer.stop()

        if hasattr(
            self,
            "play_button",
        ):
            self.play_button.setText(
                "Play"
            )

    def restart_animation(
        self,
    ) -> None:
        self.stop_animation()

        if self.current_data is None:
            return

        self.animation_elapsed_seconds = 0.0

        self.time_slider.setValue(
            0
        )

    def advance_animation(
        self,
    ) -> None:
        """
        Advance a presentation-only animation clock.

        Example at 30 FPS and 1200× playback:
        each displayed frame advances about 40 seconds of orbital time.
        The formal research sample interval remains unchanged.
        """

        if self.current_data is None:
            self.stop_animation()
            return

        data = self.current_data

        fps = float(
            self.fps_combo.currentData()
            or 30
        )

        playback_rate = float(
            self.playback_rate_combo.currentData()
            or 1200.0
        )

        total_elapsed_seconds = float(
            data[
                "elapsed_hours"
            ][
                -1
            ]
            * 3600.0
        )

        simulation_seconds_per_frame = (
            playback_rate
            / fps
        )

        self.animation_elapsed_seconds += (
            simulation_seconds_per_frame
        )

        if (
            self.animation_elapsed_seconds
            >= total_elapsed_seconds
        ):
            self.animation_elapsed_seconds = (
                total_elapsed_seconds
            )

            self.time_slider.setValue(
                self.time_slider.maximum()
            )

            self.stop_animation()
            return

        progress = (
            self.animation_elapsed_seconds
            / total_elapsed_seconds
        )

        slider_value = round(
            progress
            * self.time_slider.maximum()
        )

        self.time_slider.setValue(
            slider_value
        )