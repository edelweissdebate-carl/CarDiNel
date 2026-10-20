from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.paths import RESULTS_DIR, STATISTICS_DIR


SCOPE_LABELS = {
    "combined": "Combined",
    "sentinel1a": "Sentinel-1A",
    "sentinel1b": "Sentinel-1B",
}


# ---------------------------------------------------------------------
# Reusable metric card
# ---------------------------------------------------------------------

class MetricCard(QFrame):
    def __init__(
        self,
        title: str,
        value: str,
        subtitle: str = "",
    ) -> None:
        super().__init__()

        self.setFrameShape(
            QFrame.Shape.StyledPanel
        )

        self.setMinimumHeight(
            118
        )

        self.setStyleSheet(
            """
            MetricCard {
                border: 1px solid palette(mid);
                border-radius: 8px;
            }
            """
        )

        layout = QVBoxLayout(
            self
        )

        title_label = QLabel(
            title
        )

        title_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        title_label.setWordWrap(
            True
        )

        title_label.setStyleSheet(
            "font-size: 12px; "
            "font-weight: bold;"
        )

        value_label = QLabel(
            value
        )

        value_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        value_label.setWordWrap(
            True
        )

        value_label.setStyleSheet(
            "font-size: 27px; "
            "font-weight: bold;"
        )

        subtitle_label = QLabel(
            subtitle
        )

        subtitle_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        subtitle_label.setWordWrap(
            True
        )

        subtitle_label.setStyleSheet(
            "font-size: 10px;"
        )

        layout.addWidget(
            title_label
        )

        layout.addWidget(
            value_label,
            1,
        )

        layout.addWidget(
            subtitle_label
        )


# ---------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------

class Dashboard(QWidget):
    """
    Multi-satellite ORBIT-TRUST dashboard.

    The dashboard reads the already-generated statistics files. It does
    not perform scientific calculations itself, so refreshing it is safe
    while the research worker runs in a separate process.
    """

    def __init__(self) -> None:
        super().__init__()

        self.content_layout: (
            QVBoxLayout | None
        ) = None

        self.scope_combo: (
            QComboBox | None
        ) = None

        self.build_interface()
        self.refresh_dashboard()

    # -----------------------------------------------------------------
    # Paths
    # -----------------------------------------------------------------

    def statistics_folder(
        self,
        scope: str,
    ) -> Path:
        return (
            STATISTICS_DIR
            / scope
        )

    def summary_file(
        self,
        scope: str,
    ) -> Path:
        return (
            self.statistics_folder(
                scope
            )
            / "statistics_summary.json"
        )

    def time_error_file(
        self,
        scope: str,
    ) -> Path:
        return (
            self.statistics_folder(
                scope
            )
            / "time_error"
            / "time_error_statistics.json"
        )

    # -----------------------------------------------------------------
    # Interface
    # -----------------------------------------------------------------

    def build_interface(
        self,
    ) -> None:
        main_layout = QVBoxLayout(
            self
        )

        header_layout = QHBoxLayout()

        title = QLabel(
            "ORBIT-TRUST Dashboard"
        )

        title.setStyleSheet(
            "font-size: 26px; "
            "font-weight: bold;"
        )

        self.scope_combo = QComboBox()

        self.scope_combo.addItem(
            "Combined",
            "combined",
        )

        self.scope_combo.addItem(
            "Sentinel-1A",
            "sentinel1a",
        )

        self.scope_combo.addItem(
            "Sentinel-1B",
            "sentinel1b",
        )

        self.scope_combo.currentIndexChanged.connect(
            self.refresh_dashboard
        )

        refresh_button = QPushButton(
            "Refresh Dashboard"
        )

        refresh_button.clicked.connect(
            self.refresh_dashboard
        )

        header_layout.addWidget(
            title
        )

        header_layout.addStretch(
            1
        )

        header_layout.addWidget(
            QLabel(
                "Analysis scope:"
            )
        )

        header_layout.addWidget(
            self.scope_combo
        )

        header_layout.addWidget(
            refresh_button
        )

        subtitle = QLabel(
            "Summary of the latest ORBIT-TRUST "
            "orbit-reliability experiment."
        )

        subtitle.setWordWrap(
            True
        )

        subtitle.setStyleSheet(
            "font-size: 14px;"
        )

        main_layout.addLayout(
            header_layout
        )

        main_layout.addWidget(
            subtitle
        )

        scroll_area = QScrollArea()

        scroll_area.setWidgetResizable(
            True
        )

        content = QWidget()

        self.content_layout = QVBoxLayout(
            content
        )

        self.content_layout.setSpacing(
            14
        )

        scroll_area.setWidget(
            content
        )

        main_layout.addWidget(
            scroll_area
        )

    # -----------------------------------------------------------------
    # Data loading
    # -----------------------------------------------------------------

    def selected_scope(
        self,
    ) -> str:
        if self.scope_combo is None:
            return "combined"

        return str(
            self.scope_combo.currentData()
        )

    def load_json(
        self,
        path: Path,
    ) -> dict[str, Any] | None:
        if not path.exists():
            return None

        with path.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(
                file
            )

        if not isinstance(
            data,
            dict,
        ):
            return None

        return data

    # -----------------------------------------------------------------
    # Refresh
    # -----------------------------------------------------------------

    def refresh_dashboard(
        self,
        *_args,
    ) -> None:
        self.clear_dashboard()

        if self.content_layout is None:
            return

        scope = self.selected_scope()

        summary_path = (
            self.summary_file(
                scope
            )
        )

        if not summary_path.exists():
            self.add_no_data_message(
                scope
            )
            return

        try:
            summary = self.load_json(
                summary_path
            )

            if summary is None:
                raise ValueError(
                    "Statistics summary did not contain "
                    "a JSON object."
                )

            time_summary = (
                self.load_json(
                    self.time_error_file(
                        scope
                    )
                )
            )

            self.add_scope_header(
                scope,
                summary,
            )

            self.add_dataset_cards(
                scope,
                summary,
            )

            self.add_error_cards(
                summary
            )

            self.add_reliability_section(
                summary
            )

            self.add_time_error_section(
                time_summary
            )

            self.add_satellite_comparison_section(
                summary
            )

            self.add_component_test_section(
                summary
            )

            self.add_graphs(
                scope
            )

            self.add_completion_status(
                scope
            )

        except Exception as error:
            message = QLabel(
                "Could not load dashboard data:\n"
                f"{error}"
            )

            message.setWordWrap(
                True
            )

            message.setStyleSheet(
                "font-size: 15px; "
                "padding: 20px;"
            )

            self.content_layout.addWidget(
                message
            )

    # -----------------------------------------------------------------
    # Clearing
    # -----------------------------------------------------------------

    def clear_dashboard(
        self,
    ) -> None:
        if self.content_layout is None:
            return

        while (
            self.content_layout.count()
        ):
            item = (
                self.content_layout.takeAt(
                    0
                )
            )

            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

            child_layout = item.layout()

            if child_layout is not None:
                self.clear_layout(
                    child_layout
                )

    def clear_layout(
        self,
        layout,
    ) -> None:
        while layout.count():
            item = layout.takeAt(
                0
            )

            if (
                item.widget()
                is not None
            ):
                item.widget().deleteLater()

            if (
                item.layout()
                is not None
            ):
                self.clear_layout(
                    item.layout()
                )

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def format_number(
        self,
        value: Any,
        decimals: int = 2,
        suffix: str = "",
    ) -> str:
        if value is None:
            return "N/A"

        try:
            return (
                f"{float(value):.{decimals}f}"
                f"{suffix}"
            )
        except (
            TypeError,
            ValueError,
        ):
            return str(
                value
            )

    def format_p_value(
        self,
        value: Any,
    ) -> str:
        if value is None:
            return "N/A"

        try:
            number = float(
                value
            )
        except (
            TypeError,
            ValueError,
        ):
            return str(
                value
            )

        if number < 0.0001:
            return (
                f"{number:.2e}"
            )

        return (
            f"{number:.4f}"
        )

    def decision_text(
        self,
        value: Any,
    ) -> str:
        if value is True:
            return "Reject H₀"

        if value is False:
            return "Fail to reject H₀"

        return "Not determined"

    def section_heading(
        self,
        text: str,
    ) -> QLabel:
        heading = QLabel(
            text
        )

        heading.setStyleSheet(
            "font-size: 20px; "
            "font-weight: bold; "
            "margin-top: 10px;"
        )

        return heading

    def add_no_data_message(
        self,
        scope: str,
    ) -> None:
        if self.content_layout is None:
            return

        label = SCOPE_LABELS.get(
            scope,
            scope,
        )

        message = QLabel(
            f"No {label} statistics are available yet.\n\n"
            "Open the Research Workflow tab and run the "
            "complete research pipeline, then return here "
            "and press Refresh Dashboard."
        )

        message.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        message.setWordWrap(
            True
        )

        message.setStyleSheet(
            "font-size: 16px; "
            "padding: 40px;"
        )

        self.content_layout.addWidget(
            message
        )

        self.content_layout.addStretch(
            1
        )

    # -----------------------------------------------------------------
    # Scope / sample header
    # -----------------------------------------------------------------

    def add_scope_header(
        self,
        scope: str,
        summary: dict[str, Any],
    ) -> None:
        if self.content_layout is None:
            return

        label = SCOPE_LABELS.get(
            scope,
            scope,
        )

        scope_label = QLabel(
            f"Displaying: {label}"
        )

        scope_label.setStyleSheet(
            "font-size: 16px; "
            "font-weight: bold;"
        )

        self.content_layout.addWidget(
            scope_label
        )

        try:
            tle_count = int(
                summary.get(
                    "tle_count",
                    0,
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            tle_count = 0

        if (
            tle_count > 0
            and tle_count < 10
        ):
            warning = QLabel(
                "Small-sample diagnostic dataset: "
                "inferential p-values and confidence intervals "
                "should be treated as pipeline-validation results, "
                "not final research conclusions."
            )

            warning.setWordWrap(
                True
            )

            warning.setStyleSheet(
                "font-size: 12px; "
                "font-weight: bold; "
                "padding: 9px; "
                "border: 1px solid #d99a32; "
                "border-radius: 6px;"
            )

            self.content_layout.addWidget(
                warning
            )

    # -----------------------------------------------------------------
    # Dataset cards
    # -----------------------------------------------------------------

    def add_dataset_cards(
        self,
        scope: str,
        summary: dict[str, Any],
    ) -> None:
        if self.content_layout is None:
            return

        self.content_layout.addWidget(
            self.section_heading(
                "Dataset"
            )
        )

        counts = (
            summary.get(
                "satellite_counts",
                {}
            )
            or {}
        )

        tle_count = summary.get(
            "tle_count",
            0,
        )

        layout = QGridLayout()

        layout.addWidget(
            MetricCard(
                "Total TLE cases",
                str(
                    tle_count
                ),
                "Independent batch-analysis units",
            ),
            0,
            0,
        )

        if scope == "combined":
            s1a_count = counts.get(
                "Sentinel-1A",
                0,
            )

            s1b_count = counts.get(
                "Sentinel-1B",
                0,
            )

            layout.addWidget(
                MetricCard(
                    "Sentinel-1A",
                    str(
                        s1a_count
                    ),
                    "Daily sampled TLE cases",
                ),
                0,
                1,
            )

            layout.addWidget(
                MetricCard(
                    "Sentinel-1B",
                    str(
                        s1b_count
                    ),
                    "Daily sampled TLE cases",
                ),
                0,
                2,
            )

            layout.addWidget(
                MetricCard(
                    "Mission scope",
                    "S1A + S1B",
                    "Combined Sentinel validation",
                ),
                0,
                3,
            )

        else:
            mission = (
                "Sentinel-1A"
                if scope == "sentinel1a"
                else "Sentinel-1B"
            )

            mission_code = (
                "S1A"
                if scope == "sentinel1a"
                else "S1B"
            )

            mission_count = counts.get(
                mission,
                tle_count,
            )

            layout.addWidget(
                MetricCard(
                    "Satellite",
                    mission_code,
                    mission,
                ),
                0,
                1,
            )

            layout.addWidget(
                MetricCard(
                    "Daily samples",
                    str(
                        mission_count
                    ),
                    "Earliest valid TLE per UTC day",
                ),
                0,
                2,
            )

        self.content_layout.addLayout(
            layout
        )

    # -----------------------------------------------------------------
    # Error metrics
    # -----------------------------------------------------------------

    def add_error_cards(
        self,
        summary: dict[str, Any],
    ) -> None:
        if self.content_layout is None:
            return

        self.content_layout.addWidget(
            self.section_heading(
                "Position Error"
            )
        )

        metrics = (
            summary.get(
                "error_metrics",
                {}
            )
            or {}
        )

        total_rms = metrics.get(
            "rms_error_km",
            {},
        )

        radial = metrics.get(
            "radial_rms_km",
            {},
        )

        along = metrics.get(
            "along_track_rms_km",
            {},
        )

        cross = metrics.get(
            "cross_track_rms_km",
            {},
        )

        layout = QGridLayout()

        layout.addWidget(
            MetricCard(
                "Mean total RMS",
                self.format_number(
                    total_rms.get(
                        "mean"
                    ),
                    3,
                    " km",
                ),
                (
                    "Median: "
                    + self.format_number(
                        total_rms.get(
                            "median"
                        ),
                        3,
                        " km",
                    )
                ),
            ),
            0,
            0,
        )

        layout.addWidget(
            MetricCard(
                "Radial RMS",
                self.format_number(
                    radial.get(
                        "mean"
                    ),
                    3,
                    " km",
                ),
                "Mean across TLE cases",
            ),
            0,
            1,
        )

        layout.addWidget(
            MetricCard(
                "Along-track RMS",
                self.format_number(
                    along.get(
                        "mean"
                    ),
                    3,
                    " km",
                ),
                "Mean across TLE cases",
            ),
            0,
            2,
        )

        layout.addWidget(
            MetricCard(
                "Cross-track RMS",
                self.format_number(
                    cross.get(
                        "mean"
                    ),
                    3,
                    " km",
                ),
                "Mean across TLE cases",
            ),
            0,
            3,
        )

        ci_low = total_rms.get(
            "confidence_interval_95_low"
        )

        ci_high = total_rms.get(
            "confidence_interval_95_high"
        )

        if (
            ci_low is not None
            and ci_high is not None
        ):
            ci_label = QLabel(
                "95% confidence interval for mean total RMS: "
                f"{self.format_number(ci_low, 3)} to "
                f"{self.format_number(ci_high, 3)} km"
            )

            ci_label.setWordWrap(
                True
            )

            ci_label.setStyleSheet(
                "font-size: 12px; "
                "padding: 4px;"
            )

            self.content_layout.addLayout(
                layout
            )

            self.content_layout.addWidget(
                ci_label
            )

        else:
            self.content_layout.addLayout(
                layout
            )

    # -----------------------------------------------------------------
    # Reliability horizon
    # -----------------------------------------------------------------

    def add_reliability_section(
        self,
        summary: dict[str, Any],
    ) -> None:
        if self.content_layout is None:
            return

        self.content_layout.addWidget(
            self.section_heading(
                "Reliability Horizons"
            )
        )

        table = QTableWidget()

        table.setColumnCount(
            6
        )

        table.setHorizontalHeaderLabels(
            [
                "Threshold",
                "Exact",
                "Lower bound",
                "Crossing rate",
                "Mean exact horizon",
                "Median exact horizon",
            ]
        )

        horizons = summary.get(
            "reliability_horizons",
            [],
        )

        table.setRowCount(
            len(
                horizons
            )
        )

        for row, item in enumerate(
            horizons
        ):
            exact_stats = item.get(
                "exact_horizon_statistics"
            )

            if not isinstance(
                exact_stats,
                dict,
            ):
                exact_stats = {}

            values = [
                (
                    f"{self.format_number(item.get('threshold_km'), 0)} km"
                ),
                str(
                    item.get(
                        "exact_crossings",
                        0,
                    )
                ),
                str(
                    item.get(
                        "lower_bounds",
                        0,
                    )
                ),
                self.format_number(
                    item.get(
                        "crossing_percentage"
                    ),
                    1,
                    "%",
                ),
                self.format_number(
                    exact_stats.get(
                        "mean"
                    ),
                    3,
                    " h",
                ),
                self.format_number(
                    exact_stats.get(
                        "median"
                    ),
                    3,
                    " h",
                ),
            ]

            for column, value in enumerate(
                values
            ):
                cell = QTableWidgetItem(
                    value
                )

                cell.setTextAlignment(
                    int(
                        Qt.AlignmentFlag.AlignCenter
                    )
                )

                table.setItem(
                    row,
                    column,
                    cell,
                )

        table.resizeColumnsToContents()

        table.setAlternatingRowColors(
            True
        )

        table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )

        table.setMaximumHeight(
            210
        )

        self.content_layout.addWidget(
            table
        )

        note = QLabel(
            "Lower-bound cases are right-censored: "
            "the threshold was not crossed during the "
            "24-hour observation window."
        )

        note.setWordWrap(
            True
        )

        note.setStyleSheet(
            "font-size: 11px;"
        )

        self.content_layout.addWidget(
            note
        )

    # -----------------------------------------------------------------
    # Propagation time / error statistics
    # -----------------------------------------------------------------

    def add_time_error_section(
        self,
        time_summary: (
            dict[str, Any] | None
        ),
    ) -> None:
        if self.content_layout is None:
            return

        self.content_layout.addWidget(
            self.section_heading(
                "Propagation Time vs Position Error"
            )
        )

        if time_summary is None:
            self.content_layout.addWidget(
                QLabel(
                    "No propagation time/error "
                    "statistics were found."
                )
            )

            return

        overall = (
            time_summary.get(
                "overall",
                {}
            )
            or {}
        )

        layout = QGridLayout()

        layout.addWidget(
            MetricCard(
                "Median Spearman ρ",
                self.format_number(
                    overall.get(
                        "median_rho"
                    ),
                    4,
                ),
                (
                    "Mean ρ: "
                    + self.format_number(
                        overall.get(
                            "mean_rho"
                        ),
                        4,
                    )
                ),
            ),
            0,
            0,
        )

        layout.addWidget(
            MetricCard(
                "Wilcoxon p-value",
                self.format_p_value(
                    overall.get(
                        "wilcoxon_p_value"
                    )
                ),
                (
                    "TLE-level signed-rank test"
                ),
            ),
            0,
            1,
        )

        layout.addWidget(
            MetricCard(
                "H₀ decision",
                self.decision_text(
                    overall.get(
                        "reject_null_hypothesis"
                    )
                ),
                (
                    "α = "
                    + self.format_number(
                        overall.get(
                            "significance_level",
                            time_summary.get(
                                "significance_level"
                            ),
                        ),
                        3,
                    )
                ),
            ),
            0,
            2,
        )

        layout.addWidget(
            MetricCard(
                "Correlation direction",
                (
                    f"{overall.get('positive_correlations', 0)} + / "
                    f"{overall.get('negative_correlations', 0)} −"
                ),
                "Positive / negative TLE-level ρ values",
            ),
            0,
            3,
        )

        self.content_layout.addLayout(
            layout
        )

        by_satellite = time_summary.get(
            "by_satellite",
            [],
        )

        if (
            isinstance(
                by_satellite,
                list,
            )
            and len(
                by_satellite
            ) > 1
        ):
            table = QTableWidget()

            table.setColumnCount(
                5
            )

            table.setHorizontalHeaderLabels(
                [
                    "Satellite",
                    "TLEs",
                    "Mean ρ",
                    "Median ρ",
                    "Wilcoxon p",
                ]
            )

            table.setRowCount(
                len(
                    by_satellite
                )
            )

            for row, item in enumerate(
                by_satellite
            ):
                values = [
                    str(
                        item.get(
                            "scope",
                            "N/A",
                        )
                    ),
                    str(
                        item.get(
                            "tle_count",
                            "N/A",
                        )
                    ),
                    self.format_number(
                        item.get(
                            "mean_rho"
                        ),
                        4,
                    ),
                    self.format_number(
                        item.get(
                            "median_rho"
                        ),
                        4,
                    ),
                    self.format_p_value(
                        item.get(
                            "wilcoxon_p_value"
                        )
                    ),
                ]

                for column, value in enumerate(
                    values
                ):
                    cell = QTableWidgetItem(
                        value
                    )

                    cell.setTextAlignment(
                        int(
                            Qt.AlignmentFlag.AlignCenter
                        )
                    )

                    table.setItem(
                        row,
                        column,
                        cell,
                    )

            table.resizeColumnsToContents()

            table.setAlternatingRowColors(
                True
            )

            table.setEditTriggers(
                QTableWidget.EditTrigger.NoEditTriggers
            )

            table.setMaximumHeight(
                150
            )

            self.content_layout.addWidget(
                table
            )

    # -----------------------------------------------------------------
    # Sentinel-1A vs Sentinel-1B
    # -----------------------------------------------------------------

    def add_satellite_comparison_section(
        self,
        summary: dict[str, Any],
    ) -> None:
        comparison = summary.get(
            "satellite_rms_comparison"
        )

        if not isinstance(
            comparison,
            dict,
        ):
            return

        if self.content_layout is None:
            return

        self.content_layout.addWidget(
            self.section_heading(
                "Sentinel-1A vs Sentinel-1B"
            )
        )

        layout = QGridLayout()

        group_1 = str(
            comparison.get(
                "group_1",
                "Group 1",
            )
        )

        group_2 = str(
            comparison.get(
                "group_2",
                "Group 2",
            )
        )

        layout.addWidget(
            MetricCard(
                group_1,
                self.format_number(
                    comparison.get(
                        "group_1_median"
                    ),
                    3,
                    " km",
                ),
                (
                    "Median RMS; n = "
                    f"{comparison.get('group_1_n', 'N/A')}"
                ),
            ),
            0,
            0,
        )

        layout.addWidget(
            MetricCard(
                group_2,
                self.format_number(
                    comparison.get(
                        "group_2_median"
                    ),
                    3,
                    " km",
                ),
                (
                    "Median RMS; n = "
                    f"{comparison.get('group_2_n', 'N/A')}"
                ),
            ),
            0,
            1,
        )

        layout.addWidget(
            MetricCard(
                "Mann-Whitney U",
                self.format_number(
                    comparison.get(
                        "statistic"
                    ),
                    3,
                ),
                "Two-sided RMS comparison",
            ),
            0,
            2,
        )

        layout.addWidget(
            MetricCard(
                "p-value",
                self.format_p_value(
                    comparison.get(
                        "p_value"
                    )
                ),
                (
                    "Significant"
                    if comparison.get(
                        "significant"
                    )
                    is True
                    else "Not significant"
                ),
            ),
            0,
            3,
        )

        self.content_layout.addLayout(
            layout
        )

    # -----------------------------------------------------------------
    # RTN component tests
    # -----------------------------------------------------------------

    def add_component_test_section(
        self,
        summary: dict[str, Any],
    ) -> None:
        tests = summary.get(
            "component_error_tests"
        )

        if (
            not isinstance(
                tests,
                list,
            )
            or not tests
        ):
            return

        if self.content_layout is None:
            return

        self.content_layout.addWidget(
            self.section_heading(
                "RTN Component Statistical Tests"
            )
        )

        table = QTableWidget()

        table.setColumnCount(
            6
        )

        table.setHorizontalHeaderLabels(
            [
                "Satellite",
                "Test",
                "Comparison",
                "n",
                "p-value",
                "Significant",
            ]
        )

        table.setRowCount(
            len(
                tests
            )
        )

        for row, item in enumerate(
            tests
        ):
            values = [
                str(
                    item.get(
                        "satellite_name",
                        "All",
                    )
                ),
                str(
                    item.get(
                        "test",
                        "N/A",
                    )
                ),
                str(
                    item.get(
                        "comparison",
                        "Overall components",
                    )
                ),
                str(
                    item.get(
                        "n_tles",
                        "N/A",
                    )
                ),
                self.format_p_value(
                    item.get(
                        "bonferroni_adjusted_p",
                        item.get(
                            "p_value"
                        ),
                    )
                ),
                (
                    "Yes"
                    if item.get(
                        "significant"
                    )
                    is True
                    else "No"
                ),
            ]

            for column, value in enumerate(
                values
            ):
                cell = QTableWidgetItem(
                    value
                )

                cell.setTextAlignment(
                    int(
                        Qt.AlignmentFlag.AlignCenter
                    )
                )

                table.setItem(
                    row,
                    column,
                    cell,
                )

        table.resizeColumnsToContents()

        table.setAlternatingRowColors(
            True
        )

        table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )

        table.setMaximumHeight(
            min(
                280,
                70
                + 31
                * len(
                    tests
                ),
            )
        )

        self.content_layout.addWidget(
            table
        )

    # -----------------------------------------------------------------
    # Figures
    # -----------------------------------------------------------------

    def add_graphs(
        self,
        scope: str,
    ) -> None:
        if self.content_layout is None:
            return

        self.content_layout.addWidget(
            self.section_heading(
                "Generated Figures"
            )
        )

        folder = self.statistics_folder(
            scope
        )

        time_folder = (
            folder
            / "time_error"
        )

        graph_files = [
            (
                "RMS Error Distribution",
                folder
                / "rms_error_histogram.png",
            ),
            (
                "RMS Error by TLE Epoch",
                folder
                / "rms_error_by_epoch.png",
            ),
            (
                "Error Metric Distribution",
                folder
                / "error_metrics_boxplot.png",
            ),
            (
                "RTN Component RMS",
                folder
                / "component_rms_boxplot.png",
            ),
            (
                "Reliability Horizon Distribution",
                folder
                / "reliability_horizon_boxplot.png",
            ),
            (
                "RMS by Satellite",
                folder
                / "rms_error_by_satellite.png",
            ),
            (
                "Threshold Crossings by Satellite",
                folder
                / "threshold_crossing_by_satellite.png",
            ),
            (
                "Spearman ρ by Satellite",
                time_folder
                / "spearman_rho_by_satellite.png",
            ),
            (
                "Median Error vs Propagation Time",
                time_folder
                / "median_error_vs_time.png",
            ),
        ]

        graph_layout = QGridLayout()

        graph_number = 0

        for title, path in graph_files:
            if not path.exists():
                continue

            card = QFrame()

            card.setFrameShape(
                QFrame.Shape.StyledPanel
            )

            card_layout = QVBoxLayout(
                card
            )

            title_label = QLabel(
                title
            )

            title_label.setAlignment(
                Qt.AlignmentFlag.AlignCenter
            )

            title_label.setStyleSheet(
                "font-size: 14px; "
                "font-weight: bold;"
            )

            image_label = QLabel()

            image_label.setAlignment(
                Qt.AlignmentFlag.AlignCenter
            )

            image_label.setMinimumHeight(
                240
            )

            pixmap = QPixmap(
                str(
                    path
                )
            )

            if not pixmap.isNull():
                image_label.setPixmap(
                    pixmap.scaled(
                        500,
                        310,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

            card_layout.addWidget(
                title_label
            )

            card_layout.addWidget(
                image_label
            )

            row = (
                graph_number // 2
            )

            column = (
                graph_number % 2
            )

            graph_layout.addWidget(
                card,
                row,
                column,
            )

            graph_number += 1

        if graph_number == 0:
            self.content_layout.addWidget(
                QLabel(
                    "No generated figures were found "
                    "for this analysis scope."
                )
            )
        else:
            self.content_layout.addLayout(
                graph_layout
            )

    # -----------------------------------------------------------------
    # Completion status
    # -----------------------------------------------------------------

    def add_completion_status(
        self,
        scope: str,
    ) -> None:
        if self.content_layout is None:
            return

        self.content_layout.addWidget(
            self.section_heading(
                "Research Status"
            )
        )

        summary_exists = (
            self.summary_file(
                scope
            ).exists()
        )

        time_exists = (
            self.time_error_file(
                scope
            ).exists()
        )

        if scope == "combined":
            report_path = (
                RESULTS_DIR
                / "ORBIT_TRUST_Research_Report.pdf"
            )
        else:
            report_path = (
                RESULTS_DIR
                / (
                    "ORBIT_TRUST_Research_Report_"
                    f"{scope}.pdf"
                )
            )

        lines = [
            (
                "✓ Batch statistics available"
                if summary_exists
                else "✗ Batch statistics missing"
            ),
            (
                "✓ Time/error statistics available"
                if time_exists
                else "✗ Time/error statistics missing"
            ),
            (
                "✓ PDF research report available"
                if report_path.exists()
                else "✗ PDF research report not found"
            ),
        ]

        status = QLabel(
            "\n".join(
                lines
            )
        )

        status.setStyleSheet(
            "font-size: 13px; "
            "padding: 10px;"
        )

        self.content_layout.addWidget(
            status
        )

        self.content_layout.addStretch(
            1
        )