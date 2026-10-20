from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from PySide6.QtCore import (
    QDate,
    QProcess,
    QProcessEnvironment,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QDesktopServices,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.paths import RESULTS_DIR


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[2]
)


class ResearchWorkflowTab(QWidget):
    """
    GUI controller for the complete ORBIT-TRUST research workflow.

    The scientific pipeline runs in a completely separate OS process.
    This prevents CPU-heavy SGP4/Astropy/statistics work from blocking
    Qt's GUI event loop when the user switches tabs, resizes the window,
    or interacts with other parts of ORBIT-TRUST.
    """

    pipeline_completed = Signal()

    def __init__(self) -> None:
        super().__init__()

        self.process = QProcess(
            self
        )

        self.process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )

        environment = (
            QProcessEnvironment.systemEnvironment()
        )

        # Force Python print() output to arrive in the GUI immediately.
        environment.insert(
            "PYTHONUNBUFFERED",
            "1",
        )

        # Force UTF-8 in the isolated worker process. This prevents
        # Windows' legacy cp1252 console encoding from crashing on
        # Unicode characters printed by the pipeline.
        environment.insert(
            "PYTHONIOENCODING",
            "utf-8",
        )

        environment.insert(
            "PYTHONUTF8",
            "1",
        )

        self.process.setProcessEnvironment(
            environment
        )

        self.process.readyReadStandardOutput.connect(
            self.read_process_output
        )

        self.process.finished.connect(
            self.process_finished
        )

        self.process.errorOccurred.connect(
            self.process_error
        )

        self.build_interface()

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
            "Research Workflow"
        )

        title.setStyleSheet(
            "font-size: 24px; "
            "font-weight: bold;"
        )

        description = QLabel(
            "Configure and run the complete ORBIT-TRUST research "
            "experiment. The scientific workload runs in a separate "
            "process so the application remains responsive while "
            "the pipeline is operating."
        )

        description.setWordWrap(
            True
        )

        main_layout.addWidget(
            title
        )

        main_layout.addWidget(
            description
        )

        main_layout.addWidget(
            self.create_configuration_group()
        )

        main_layout.addWidget(
            self.create_run_group()
        )

        self.status_label = QLabel(
            "Status: Ready"
        )

        self.status_label.setStyleSheet(
            "font-size: 14px; "
            "font-weight: bold; "
            "padding: 6px;"
        )

        main_layout.addWidget(
            self.status_label
        )

        console_group = QGroupBox(
            "Pipeline Output"
        )

        console_layout = QVBoxLayout(
            console_group
        )

        self.console = QPlainTextEdit()

        self.console.setReadOnly(
            True
        )

        self.console.setPlaceholderText(
            "Research-pipeline output will appear here."
        )

        self.console.setStyleSheet(
            "font-family: Consolas, "
            "'Courier New', monospace; "
            "font-size: 12px;"
        )

        console_layout.addWidget(
            self.console
        )

        main_layout.addWidget(
            console_group,
            1,
        )

        bottom_layout = QHBoxLayout()

        self.clear_button = QPushButton(
            "Clear Output"
        )

        self.clear_button.clicked.connect(
            self.console.clear
        )

        self.open_results_button = QPushButton(
            "Open Results Folder"
        )

        self.open_results_button.clicked.connect(
            self.open_results_folder
        )

        bottom_layout.addWidget(
            self.clear_button
        )

        bottom_layout.addStretch(
            1
        )

        bottom_layout.addWidget(
            self.open_results_button
        )

        main_layout.addLayout(
            bottom_layout
        )

    def create_configuration_group(
        self,
    ) -> QGroupBox:
        group = QGroupBox(
            "Experiment Configuration"
        )

        form = QFormLayout(
            group
        )

        self.satellite_combo = QComboBox()

        self.satellite_combo.addItem(
            "Sentinel-1A + Sentinel-1B",
            "both",
        )

        self.satellite_combo.addItem(
            "Sentinel-1A only",
            "sentinel1a",
        )

        self.satellite_combo.addItem(
            "Sentinel-1B only",
            "sentinel1b",
        )

        self.start_date_edit = QDateEdit()

        self.start_date_edit.setCalendarPopup(
            True
        )

        self.start_date_edit.setDisplayFormat(
            "yyyy-MM-dd"
        )

        self.start_date_edit.setDate(
            QDate(
                2021,
                10,
                1,
            )
        )

        self.end_date_edit = QDateEdit()

        self.end_date_edit.setCalendarPopup(
            True
        )

        self.end_date_edit.setDisplayFormat(
            "yyyy-MM-dd"
        )

        self.end_date_edit.setDate(
            QDate(
                2021,
                10,
                2,
            )
        )

        self.alpha_spin = QDoubleSpinBox()

        self.alpha_spin.setDecimals(
            3
        )

        self.alpha_spin.setRange(
            0.001,
            0.999,
        )

        self.alpha_spin.setSingleStep(
            0.01
        )

        self.alpha_spin.setValue(
            0.05
        )

        self.force_tle_checkbox = QCheckBox(
            "Force fresh historical TLE download"
        )

        self.force_tle_checkbox.setChecked(
            False
        )

        self.force_tle_checkbox.setToolTip(
            "Leave this unchecked during normal use so "
            "ORBIT-TRUST can reuse cached Space-Track data."
        )

        sampling_rule = QLabel(
            "Earliest valid TLE epoch per UTC calendar day"
        )

        sampling_rule.setWordWrap(
            True
        )

        form.addRow(
            "Satellite scope:",
            self.satellite_combo,
        )

        form.addRow(
            "First study date:",
            self.start_date_edit,
        )

        form.addRow(
            "Last study date:",
            self.end_date_edit,
        )

        form.addRow(
            "Significance level (α):",
            self.alpha_spin,
        )

        form.addRow(
            "Daily sampling rule:",
            sampling_rule,
        )

        form.addRow(
            "Propagation window:",
            QLabel(
                "24 hours per sampled TLE"
            ),
        )

        form.addRow(
            "Reliability thresholds:",
            QLabel(
                "1 km, 2 km, 5 km, and 10 km"
            ),
        )

        form.addRow(
            "",
            self.force_tle_checkbox,
        )

        return group

    def create_run_group(
        self,
    ) -> QGroupBox:
        group = QGroupBox(
            "Automated Research Pipeline"
        )

        layout = QVBoxLayout(
            group
        )

        explanation = QLabel(
            "One run performs data acquisition, daily TLE sampling, "
            "24-hour reliability analysis, detailed time-series export, "
            "statistics, and PDF report generation."
        )

        explanation.setWordWrap(
            True
        )

        self.run_button = QPushButton(
            "Run Complete Research Pipeline"
        )

        self.run_button.setMinimumHeight(
            52
        )

        self.run_button.clicked.connect(
            self.run_complete_pipeline
        )

        self.progress_bar = QProgressBar()

        self.progress_bar.setRange(
            0,
            1,
        )

        self.progress_bar.setValue(
            0
        )

        self.progress_bar.setFormat(
            "Ready"
        )

        layout.addWidget(
            explanation
        )

        layout.addWidget(
            self.run_button
        )

        layout.addWidget(
            self.progress_bar
        )

        return group

    # -----------------------------------------------------------------
    # Experiment settings
    # -----------------------------------------------------------------

    def selected_scope(
        self,
    ) -> str:
        return str(
            self.satellite_combo.currentData()
        )

    def selected_dates(
        self,
    ) -> tuple[
        date,
        date,
    ]:
        start_qdate = (
            self.start_date_edit.date()
        )

        end_qdate = (
            self.end_date_edit.date()
        )

        start_date = date(
            start_qdate.year(),
            start_qdate.month(),
            start_qdate.day(),
        )

        end_date = date(
            end_qdate.year(),
            end_qdate.month(),
            end_qdate.day(),
        )

        return (
            start_date,
            end_date,
        )

    def validate_configuration(
        self,
    ) -> bool:
        start_date, end_date = (
            self.selected_dates()
        )

        if end_date < start_date:
            QMessageBox.warning(
                self,
                "Invalid Date Range",
                "The last study date cannot be earlier "
                "than the first study date.",
            )

            return False

        return True

    # -----------------------------------------------------------------
    # Worker process command
    # -----------------------------------------------------------------

    def build_worker_command(
        self,
        scope: str,
        start_date: date,
        end_date: date,
        alpha: float,
        force_tles: bool,
    ) -> tuple[
        str,
        list[str],
    ]:
        worker_arguments = [
            "--pipeline-worker",
            "--satellite",
            scope,
            "--start",
            start_date.isoformat(),
            "--end",
            end_date.isoformat(),
            "--alpha",
            f"{alpha:.6f}",
        ]

        if force_tles:
            worker_arguments.append(
                "--force-tles"
            )

        # In a PyInstaller build, the ORBIT-TRUST executable launches
        # itself in worker mode.
        if getattr(
            sys,
            "frozen",
            False,
        ):
            return (
                sys.executable,
                worker_arguments,
            )

        # During development, start a new Python interpreter and route
        # it through src.main's --pipeline-worker branch.
        return (
            sys.executable,
            [
                "-m",
                "src.main",
                *worker_arguments,
            ],
        )

    # -----------------------------------------------------------------
    # Pipeline execution
    # -----------------------------------------------------------------

    @Slot()
    def run_complete_pipeline(
        self,
    ) -> None:
        if (
            self.process.state()
            != QProcess.ProcessState.NotRunning
        ):
            QMessageBox.warning(
                self,
                "Pipeline Already Running",
                "Wait for the current research pipeline "
                "to finish before starting another run.",
            )

            return

        if not self.validate_configuration():
            return

        scope = (
            self.selected_scope()
        )

        start_date, end_date = (
            self.selected_dates()
        )

        alpha = float(
            self.alpha_spin.value()
        )

        force_tles = (
            self.force_tle_checkbox.isChecked()
        )

        scope_label = (
            self.satellite_combo.currentText()
        )

        confirmation = QMessageBox.question(
            self,
            "Run Research Pipeline",
            (
                "Start the complete ORBIT-TRUST experiment?\n\n"
                f"Satellite scope: {scope_label}\n"
                f"Study dates: {start_date.isoformat()} "
                f"through {end_date.isoformat()}\n"
                f"Alpha: {alpha:.3f}\n\n"
                "The research calculations will run in a separate "
                "process so you may continue navigating the GUI."
            ),
        )

        if (
            confirmation
            != QMessageBox.StandardButton.Yes
        ):
            return

        program, arguments = (
            self.build_worker_command(
                scope=scope,
                start_date=start_date,
                end_date=end_date,
                alpha=alpha,
                force_tles=force_tles,
            )
        )

        self.console.appendPlainText(
            "\n"
            + "=" * 72
            + "\nSTARTING ISOLATED ORBIT-TRUST RESEARCH PROCESS"
            + "\n"
            + "=" * 72
            + "\n"
            + f"Scope: {scope_label}\n"
            + f"Study dates: {start_date.isoformat()} "
            + f"through {end_date.isoformat()}\n"
            + f"Alpha: {alpha:.3f}\n"
        )

        self.set_running_state(
            True
        )

        self.process.setWorkingDirectory(
            str(
                PROJECT_ROOT
            )
        )

        self.process.start(
            program,
            arguments,
        )

        if not self.process.waitForStarted(
            5000
        ):
            self.set_running_state(
                False
            )

            QMessageBox.critical(
                self,
                "Worker Process Error",
                (
                    "ORBIT-TRUST could not start the isolated "
                    "research worker process."
                ),
            )

    @Slot()
    def read_process_output(
        self,
    ) -> None:
        text = bytes(
            self.process.readAllStandardOutput()
        ).decode(
            "utf-8",
            errors="replace",
        )

        if not text:
            return

        cursor = (
            self.console.textCursor()
        )

        cursor.movePosition(
            QTextCursor.MoveOperation.End
        )

        cursor.insertText(
            text
        )

        self.console.setTextCursor(
            cursor
        )

        self.console.ensureCursorVisible()

    @Slot(
        int,
        QProcess.ExitStatus,
    )
    def process_finished(
        self,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        # Read anything still waiting in the pipe.
        self.read_process_output()

        completed_successfully = (
            exit_status
            == QProcess.ExitStatus.NormalExit
            and exit_code == 0
        )

        self.set_controls_enabled(
            True
        )

        self.progress_bar.setRange(
            0,
            1,
        )

        if completed_successfully:
            self.status_label.setText(
                "Status: Research pipeline completed successfully"
            )

            self.progress_bar.setValue(
                1
            )

            self.progress_bar.setFormat(
                "Complete"
            )

            self.console.appendPlainText(
                "\n"
                + "=" * 72
                + "\nISOLATED PIPELINE COMPLETED SUCCESSFULLY"
                + "\n"
                + "=" * 72
            )

            self.pipeline_completed.emit()

            QMessageBox.information(
                self,
                "Research Pipeline Complete",
                (
                    "The ORBIT-TRUST research pipeline "
                    "finished successfully.\n\n"
                    "The results folder contains the "
                    "CSV datasets, statistics, figures, "
                    "and PDF report."
                ),
            )

        else:
            self.status_label.setText(
                "Status: Research pipeline failed"
            )

            self.progress_bar.setValue(
                0
            )

            self.progress_bar.setFormat(
                "Failed"
            )

            self.console.appendPlainText(
                "\n"
                + "=" * 72
                + f"\nPIPELINE FAILED — EXIT CODE {exit_code}"
                + "\n"
                + "=" * 72
            )

            QMessageBox.critical(
                self,
                "Research Pipeline Failed",
                (
                    "The research worker process did not "
                    "finish successfully.\n\n"
                    "Check the Pipeline Output box for "
                    "the full error information."
                ),
            )

    @Slot(
        QProcess.ProcessError,
    )
    def process_error(
        self,
        error: QProcess.ProcessError,
    ) -> None:
        self.console.appendPlainText(
            "\n[PROCESS ERROR] "
            + self.process.errorString()
        )

        # FailedToStart is not followed by a normal finished signal.
        if (
            error
            == QProcess.ProcessError.FailedToStart
        ):
            self.set_running_state(
                False
            )

    # -----------------------------------------------------------------
    # UI state
    # -----------------------------------------------------------------

    def set_running_state(
        self,
        running: bool,
    ) -> None:
        if running:
            self.status_label.setText(
                "Status: Research pipeline running in separate process..."
            )

            self.progress_bar.setRange(
                0,
                0,
            )

            self.progress_bar.setFormat(
                "Running..."
            )

            self.set_controls_enabled(
                False
            )

        else:
            self.status_label.setText(
                "Status: Ready"
            )

            self.progress_bar.setRange(
                0,
                1,
            )

            self.progress_bar.setValue(
                0
            )

            self.progress_bar.setFormat(
                "Ready"
            )

            self.set_controls_enabled(
                True
            )

    def set_controls_enabled(
        self,
        enabled: bool,
    ) -> None:
        self.satellite_combo.setEnabled(
            enabled
        )

        self.start_date_edit.setEnabled(
            enabled
        )

        self.end_date_edit.setEnabled(
            enabled
        )

        self.alpha_spin.setEnabled(
            enabled
        )

        self.force_tle_checkbox.setEnabled(
            enabled
        )

        self.run_button.setEnabled(
            enabled
        )

        # These two do not affect the experiment itself, but keeping
        # them active while the worker runs is useful.
        self.clear_button.setEnabled(
            True
        )

        self.open_results_button.setEnabled(
            True
        )

    # -----------------------------------------------------------------
    # Utilities
    # -----------------------------------------------------------------

    @Slot()
    def open_results_folder(
        self,
    ) -> None:
        RESULTS_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        opened = (
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(
                    str(
                        RESULTS_DIR.resolve()
                    )
                )
            )
        )

        if not opened:
            QMessageBox.warning(
                self,
                "Could Not Open Folder",
                (
                    "The results folder could not be "
                    "opened automatically.\n\n"
                    f"Path:\n{RESULTS_DIR}"
                ),
            )