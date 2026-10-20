from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.propagator import PropagationResult, propagate_tle


DEFAULT_SATELLITE_NAME = "ISS (ZARYA)"

DEFAULT_TLE_LINE_1 = (
    "1 25544U 98067A   24150.50000000  "
    ".00016717  00000-0  30270-3 0  9995"
)

DEFAULT_TLE_LINE_2 = (
    "2 25544  51.6400  60.0000 0005000  "
    "40.0000 320.0000 15.50000000450000"
)


class PropagationTab(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.result: PropagationResult | None = None
        self.build_interface()

    def build_interface(self) -> None:
        main_layout = QVBoxLayout(self)

        title = QLabel("TLE Orbit Propagation")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        main_layout.addWidget(title)

        controls_layout = QHBoxLayout()
        controls_layout.addWidget(self.create_tle_group(), 2)
        controls_layout.addWidget(self.create_settings_group(), 1)
        main_layout.addLayout(controls_layout)

        button_layout = QHBoxLayout()

        self.load_button = QPushButton("Load TLE File")
        self.load_button.clicked.connect(self.load_tle_file)

        self.run_button = QPushButton("Run Propagation")
        self.run_button.setMinimumHeight(40)
        self.run_button.clicked.connect(self.run_propagation)

        self.export_button = QPushButton("Export CSV")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_csv)

        button_layout.addWidget(self.load_button)
        button_layout.addWidget(self.run_button, 2)
        button_layout.addWidget(self.export_button)

        main_layout.addLayout(button_layout)

        self.summary_label = QLabel(
            "No propagation has been performed yet."
        )
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(
            "padding: 8px; font-size: 14px;"
        )
        main_layout.addWidget(self.summary_label)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "Altitude", units="km")
        self.plot.setLabel(
            "bottom",
            "Time Since TLE Epoch",
            units="hours",
        )
        self.plot.showGrid(x=True, y=True)
        main_layout.addWidget(self.plot, 2)

        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            [
                "Time UTC",
                "X km",
                "Y km",
                "Z km",
                "VX km/s",
                "VY km/s",
                "VZ km/s",
                "Altitude km",
                "Speed km/s",
            ]
        )

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )

        main_layout.addWidget(self.table, 2)

    def create_tle_group(self) -> QGroupBox:
        group = QGroupBox("Two-Line Element Set")
        layout = QVBoxLayout(group)

        layout.addWidget(QLabel("Satellite name"))

        self.name_input = QLineEdit(DEFAULT_SATELLITE_NAME)
        layout.addWidget(self.name_input)

        layout.addWidget(QLabel("TLE Line 1"))

        self.line_1_input = QPlainTextEdit()
        self.line_1_input.setMaximumHeight(55)
        self.line_1_input.setPlainText(DEFAULT_TLE_LINE_1)
        layout.addWidget(self.line_1_input)

        layout.addWidget(QLabel("TLE Line 2"))

        self.line_2_input = QPlainTextEdit()
        self.line_2_input.setMaximumHeight(55)
        self.line_2_input.setPlainText(DEFAULT_TLE_LINE_2)
        layout.addWidget(self.line_2_input)

        return group

    def create_settings_group(self) -> QGroupBox:
        group = QGroupBox("Propagation Settings")
        form = QFormLayout(group)

        self.duration_input = QDoubleSpinBox()
        self.duration_input.setRange(0.1, 720.0)
        self.duration_input.setValue(24.0)
        self.duration_input.setSuffix(" hours")

        self.step_input = QSpinBox()
        self.step_input.setRange(1, 1440)
        self.step_input.setValue(10)
        self.step_input.setSuffix(" minutes")

        form.addRow("Duration:", self.duration_input)
        form.addRow("Step size:", self.step_input)

        return group

    def load_tle_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open TLE File",
            "",
            "Text files (*.txt *.tle);;All files (*.*)",
        )

        if not file_path:
            return

        try:
            lines = [
                line.strip()
                for line in Path(file_path).read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]

            if len(lines) == 2:
                satellite_name = Path(file_path).stem
                line_1, line_2 = lines

            elif len(lines) >= 3:
                satellite_name = lines[0]
                line_1 = lines[1]
                line_2 = lines[2]

            else:
                raise ValueError(
                    "The file must contain either two TLE lines or "
                    "a satellite name followed by two TLE lines."
                )

            self.name_input.setText(satellite_name)
            self.line_1_input.setPlainText(line_1)
            self.line_2_input.setPlainText(line_2)

        except Exception as error:
            QMessageBox.critical(
                self,
                "TLE File Error",
                str(error),
            )

    def run_propagation(self) -> None:
        try:
            self.run_button.setEnabled(False)
            self.run_button.setText("Propagating...")

            self.result = propagate_tle(
                satellite_name=self.name_input.text(),
                tle_line_1=self.line_1_input.toPlainText(),
                tle_line_2=self.line_2_input.toPlainText(),
                duration_hours=self.duration_input.value(),
                step_minutes=self.step_input.value(),
            )

            self.update_table()
            self.update_plot()
            self.update_summary()
            self.export_button.setEnabled(True)

        except Exception as error:
            QMessageBox.critical(
                self,
                "Propagation Error",
                str(error),
            )

        finally:
            self.run_button.setEnabled(True)
            self.run_button.setText("Run Propagation")

    def update_table(self) -> None:
        if self.result is None:
            return

        states = self.result.states
        self.table.setRowCount(len(states))

        for row, state in enumerate(states):
            values = [
                state.time.strftime("%Y-%m-%d %H:%M:%S"),
                f"{state.x_km:.3f}",
                f"{state.y_km:.3f}",
                f"{state.z_km:.3f}",
                f"{state.vx_km_s:.6f}",
                f"{state.vy_km_s:.6f}",
                f"{state.vz_km_s:.6f}",
                f"{state.altitude_km:.3f}",
                f"{state.speed_km_s:.6f}",
            ]

            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter
                )
                self.table.setItem(row, column, item)

    def update_plot(self) -> None:
        self.plot.clear()

        if self.result is None or not self.result.states:
            return

        states = self.result.states
        start_time: datetime = states[0].time

        hours = [
            (state.time - start_time).total_seconds() / 3600
            for state in states
        ]

        altitudes = [state.altitude_km for state in states]

        self.plot.plot(
            hours,
            altitudes,
            pen=pg.mkPen(width=2),
        )

    def update_summary(self) -> None:
        if self.result is None:
            return

        states = self.result.states
        altitudes = [state.altitude_km for state in states]
        speeds = [state.speed_km_s for state in states]

        minimum_altitude = min(altitudes)
        maximum_altitude = max(altitudes)
        average_altitude = sum(altitudes) / len(altitudes)
        average_speed = sum(speeds) / len(speeds)

        summary = (
            f"Satellite: {self.result.satellite_name} | "
            f"TLE epoch: "
            f"{self.result.tle_epoch.strftime('%Y-%m-%d %H:%M:%S UTC')} | "
            f"States: {len(states)} | "
            f"Perigee: {minimum_altitude:.2f} km | "
            f"Apogee: {maximum_altitude:.2f} km | "
            f"Average altitude: {average_altitude:.2f} km | "
            f"Average speed: {average_speed:.4f} km/s"
        )

        self.summary_label.setText(summary)

    def export_csv(self) -> None:
        if self.result is None:
            return

        default_name = (
            self.result.satellite_name
            .lower()
            .replace(" ", "_")
            .replace("(", "")
            .replace(")", "")
        )

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Propagation Results",
            f"{default_name}_propagation.csv",
            "CSV files (*.csv)",
        )

        if not file_path:
            return

        try:
            with open(
                file_path,
                "w",
                newline="",
                encoding="utf-8",
            ) as csv_file:
                writer = csv.writer(csv_file)

                writer.writerow(
                    [
                        "time_utc",
                        "x_km",
                        "y_km",
                        "z_km",
                        "vx_km_s",
                        "vy_km_s",
                        "vz_km_s",
                        "altitude_km",
                        "speed_km_s",
                    ]
                )

                for state in self.result.states:
                    writer.writerow(
                        [
                            state.time.isoformat(),
                            state.x_km,
                            state.y_km,
                            state.z_km,
                            state.vx_km_s,
                            state.vy_km_s,
                            state.vz_km_s,
                            state.altitude_km,
                            state.speed_km_s,
                        ]
                    )

            QMessageBox.information(
                self,
                "Export Complete",
                f"Propagation results were saved to:\n{file_path}",
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Export Error",
                str(error),
            )