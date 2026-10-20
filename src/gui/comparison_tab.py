from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.comparator import (
    ComparisonResult,
    compare_orbits,
    load_orbit_csv,
)
from src.core.reliability import (
    ReliabilityResult,
    calculate_reliability_horizon,
)


class ComparisonTab(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.result: ComparisonResult | None = None
        self.reliability_result: ReliabilityResult | None = None

        self.build_interface()

    def build_interface(self) -> None:
        main_layout = QVBoxLayout(self)

        title = QLabel("Orbit Comparison")
        title.setStyleSheet(
            "font-size: 24px; font-weight: bold;"
        )
        main_layout.addWidget(title)

        description = QLabel(
            "Compare an SGP4 propagation with a precise reference orbit "
            "and calculate the reliability horizon."
        )
        description.setWordWrap(True)
        main_layout.addWidget(description)

        main_layout.addWidget(self.create_file_group())
        main_layout.addWidget(self.create_reliability_group())

        self.compare_button = QPushButton("Compare Orbits")
        self.compare_button.setMinimumHeight(42)
        self.compare_button.clicked.connect(
            self.run_comparison
        )
        main_layout.addWidget(self.compare_button)

        self.summary_label = QLabel(
            "No comparison has been performed yet."
        )
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(
            "padding: 8px; font-size: 14px;"
        )
        main_layout.addWidget(self.summary_label)

        self.plot = pg.PlotWidget()
        self.plot.setLabel(
            "left",
            "Position Error",
            units="km",
        )
        self.plot.setLabel(
            "bottom",
            "Time Since First Record",
            units="hours",
        )
        self.plot.showGrid(x=True, y=True)
        self.plot.addLegend()

        main_layout.addWidget(self.plot, 2)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            [
                "Time UTC",
                "Total Error km",
                "Radial km",
                "Along-track km",
                "Cross-track km",
            ]
        )

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )

        main_layout.addWidget(self.table, 2)

    def create_file_group(self) -> QGroupBox:
        group = QGroupBox("Orbit Files")
        layout = QGridLayout(group)

        self.predicted_path_input = QLineEdit()
        self.predicted_path_input.setReadOnly(True)

        predicted_button = QPushButton(
            "Select SGP4 CSV"
        )
        predicted_button.clicked.connect(
            self.select_predicted_file
        )

        self.reference_path_input = QLineEdit()
        self.reference_path_input.setReadOnly(True)

        reference_button = QPushButton(
            "Select Reference CSV"
        )
        reference_button.clicked.connect(
            self.select_reference_file
        )

        layout.addWidget(
            QLabel("Predicted orbit:"),
            0,
            0,
        )
        layout.addWidget(
            self.predicted_path_input,
            0,
            1,
        )
        layout.addWidget(
            predicted_button,
            0,
            2,
        )

        layout.addWidget(
            QLabel("Reference orbit:"),
            1,
            0,
        )
        layout.addWidget(
            self.reference_path_input,
            1,
            1,
        )
        layout.addWidget(
            reference_button,
            1,
            2,
        )

        return group

    def create_reliability_group(self) -> QGroupBox:
        group = QGroupBox("Reliability Horizon Settings")
        layout = QGridLayout(group)

        self.threshold_input = QDoubleSpinBox()
        self.threshold_input.setRange(
            0.001,
            100000.0,
        )
        self.threshold_input.setDecimals(3)
        self.threshold_input.setValue(5.0)
        self.threshold_input.setSuffix(" km")

        explanation = QLabel(
            "The reliability horizon is the time until the total "
            "position error first reaches or exceeds the selected limit."
        )
        explanation.setWordWrap(True)

        layout.addWidget(
            QLabel("Error limit:"),
            0,
            0,
        )
        layout.addWidget(
            self.threshold_input,
            0,
            1,
        )
        layout.addWidget(
            explanation,
            1,
            0,
            1,
            2,
        )

        return group

    def select_predicted_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SGP4 Orbit CSV",
            "",
            "CSV files (*.csv);;All files (*.*)",
        )

        if file_path:
            self.predicted_path_input.setText(file_path)

    def select_reference_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Reference Orbit CSV",
            "",
            "CSV files (*.csv);;All files (*.*)",
        )

        if file_path:
            self.reference_path_input.setText(file_path)

    def run_comparison(self) -> None:
        predicted_path = self.predicted_path_input.text()
        reference_path = self.reference_path_input.text()

        if not predicted_path or not reference_path:
            QMessageBox.warning(
                self,
                "Missing Files",
                "Select both the predicted and reference CSV files.",
            )
            return

        try:
            self.compare_button.setEnabled(False)
            self.compare_button.setText("Comparing...")

            predicted_records = load_orbit_csv(
                Path(predicted_path)
            )

            reference_records = load_orbit_csv(
                Path(reference_path)
            )

            self.result = compare_orbits(
                predicted_records,
                reference_records,
            )

            self.reliability_result = (
                calculate_reliability_horizon(
                    errors=self.result.errors,
                    threshold_km=self.threshold_input.value(),
                )
            )

            self.update_summary()
            self.update_plot()
            self.update_table()

        except Exception as error:
            QMessageBox.critical(
                self,
                "Comparison Error",
                str(error),
            )

        finally:
            self.compare_button.setEnabled(True)
            self.compare_button.setText("Compare Orbits")

    def update_summary(self) -> None:
        if self.result is None:
            return

        comparison_text = (
            f"Matched records: {len(self.result.errors)} | "
            f"Mean error: "
            f"{self.result.mean_error_km:.6f} km | "
            f"Maximum error: "
            f"{self.result.maximum_error_km:.6f} km | "
            f"RMS error: "
            f"{self.result.rms_error_km:.6f} km"
        )

        if self.reliability_result is None:
            self.summary_label.setText(
                comparison_text
            )
            return

        reliability = self.reliability_result

        if (
            reliability.crossed_threshold
            and reliability.horizon_hours is not None
        ):
            horizon_text = (
                f"Reliability horizon at "
                f"{reliability.threshold_km:.3f} km: "
                f"{reliability.horizon_hours:.3f} hours"
            )

            if reliability.crossing_time is not None:
                horizon_text += (
                    f" | Threshold crossing: "
                    f"{reliability.crossing_time.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                )
        else:
            horizon_text = (
                f"The error remained below "
                f"{reliability.threshold_km:.3f} km for the entire "
                f"{reliability.maximum_observed_hours:.3f}-hour dataset. "
                f"The true reliability horizon is longer than the "
                f"available observation period."
            )

        self.summary_label.setText(
            comparison_text + "\n" + horizon_text
        )

    def update_plot(self) -> None:
        self.plot.clear()
        self.plot.addLegend()

        if self.result is None:
            return

        errors = self.result.errors
        start_time: datetime = errors[0].time

        hours = [
            (error.time - start_time).total_seconds() / 3600
            for error in errors
        ]

        total_errors = [
            error.total_error_km
            for error in errors
        ]

        radial_errors = [
            abs(error.radial_error_km)
            for error in errors
        ]

        along_track_errors = [
            abs(error.along_track_error_km)
            for error in errors
        ]

        cross_track_errors = [
            abs(error.cross_track_error_km)
            for error in errors
        ]

        self.plot.plot(
            hours,
            total_errors,
            pen=pg.mkPen(width=3),
            name="Total",
        )

        self.plot.plot(
            hours,
            radial_errors,
            pen=pg.mkPen(width=2),
            name="Radial",
        )

        self.plot.plot(
            hours,
            along_track_errors,
            pen=pg.mkPen(width=2),
            name="Along-track",
        )

        self.plot.plot(
            hours,
            cross_track_errors,
            pen=pg.mkPen(width=2),
            name="Cross-track",
        )

        if self.reliability_result is not None:
            threshold_line = pg.InfiniteLine(
                pos=self.reliability_result.threshold_km,
                angle=0,
                movable=False,
                label=(
                    f"{self.reliability_result.threshold_km:.3f} km limit"
                ),
            )
            self.plot.addItem(threshold_line)

            if (
                self.reliability_result.crossed_threshold
                and self.reliability_result.horizon_hours is not None
            ):
                horizon_line = pg.InfiniteLine(
                    pos=self.reliability_result.horizon_hours,
                    angle=90,
                    movable=False,
                    label=(
                        f"{self.reliability_result.horizon_hours:.2f} h"
                    ),
                )
                self.plot.addItem(horizon_line)

    def update_table(self) -> None:
        if self.result is None:
            return

        errors = self.result.errors
        self.table.setRowCount(len(errors))

        for row, error in enumerate(errors):
            values = [
                error.time.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                f"{error.total_error_km:.6f}",
                f"{error.radial_error_km:.6f}",
                f"{error.along_track_error_km:.6f}",
                f"{error.cross_track_error_km:.6f}",
            ]

            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter
                )
                self.table.setItem(
                    row,
                    column,
                    item,
                )