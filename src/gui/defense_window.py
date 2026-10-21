"""Offline presentation of saved results; this module never runs propagation."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QMainWindow, QMessageBox, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from src.core.result_archive import ResultArchive, SATELLITES
from src.gui.earth_error_view import EarthErrorView

COLORS = {1: '#b45309', 2: '#c2415c', 5: '#6d4cc4', 10: '#50677d'}


def label(text, name=None):
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.PlainText)
    if name:
        widget.setObjectName(name)
    return widget


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QTableWidget.NoEditTriggers)
    widget.setSelectionBehavior(QTableWidget.SelectRows)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return widget


def fill(widget, rows):
    widget.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            widget.setItem(i, j, item)


class DefenseWindow(QMainWindow):
    def __init__(self, results=None):
        super().__init__()
        self.archive = None
        self.current_case = None
        self.current_series = None
        self.last_error = None
        self.setWindowTitle('CarDiNel | Defense Results')
        self.resize(1240, 820)
        self.setMinimumSize(920, 700)
        self.setStyleSheet('''
            QMainWindow, QWidget { background:#f4f7fb; color:#172b43; font-size:13px; }
            QLabel#title { font-size:26px; font-weight:700; }
            QLabel#subtitle { color:#4c6075; }
            QLabel#metrics { background:white; border:1px solid #d9e2ed; border-radius:8px; padding:14px; font-size:18px; }
            QLabel#quality { padding:9px; border-radius:6px; }
            QPushButton { background:#17476c; color:white; border:0; border-radius:5px; padding:9px 14px; }
            QPushButton:disabled { background:#8b99a8; }
            QComboBox { background:white; padding:6px; min-width:140px; }
            QTabWidget::pane { border:1px solid #d9e2ed; background:white; }
            QTabBar::tab { padding:10px 16px; }
            QTabBar::tab:selected { background:white; color:#17476c; }
            QTableWidget, QTextEdit { background:white; border:1px solid #d9e2ed; }
            QHeaderView::section { background:#e8eff6; padding:7px; border:0; }
        ''')
        root = QWidget(); layout = QVBoxLayout(root); layout.setContentsMargins(22, 18, 22, 12); layout.setSpacing(10)
        heading = QHBoxLayout(); heading.addWidget(label('CarDiNel · Defense Results', 'title'), 1)
        open_zip = QPushButton('Open results ZIP'); open_zip.clicked.connect(self.choose_zip)
        open_folder = QPushButton('Open results folder'); open_folder.clicked.connect(self.choose_folder)
        heading.addWidget(open_zip); heading.addWidget(open_folder); layout.addLayout(heading)
        layout.addWidget(label('OFFLINE STUDY VIEWER  •  Historical TLE / SGP4 versus precise reference orbits', 'subtitle'))
        self.dataset_label = label('Open a saved study to begin.'); layout.addWidget(self.dataset_label)
        controls = QHBoxLayout(); controls.addWidget(label('Satellite'))
        self.satellite_combo = QComboBox(); controls.addWidget(self.satellite_combo)
        controls.addWidget(label('TLE epoch (UTC)')); self.case_combo = QComboBox(); self.case_combo.setMinimumWidth(260); controls.addWidget(self.case_combo)
        self.screened = QCheckBox('Exclude flagged / unknown reference quality'); controls.addWidget(self.screened); controls.addStretch(); layout.addLayout(controls)
        self.metrics = label('No case selected', 'metrics'); layout.addWidget(self.metrics)
        self.quality = label('Reference-quality annotations are shown when supplied with the study.', 'quality'); layout.addWidget(self.quality)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs, 1)
        self.earth_view = EarthErrorView(); self.tabs.addTab(self.earth_view, '3D Earth')
        plot_page = QWidget(); pl = QVBoxLayout(plot_page)
        self.figure = Figure(figsize=(9, 3.6), layout='constrained', facecolor='white')
        self.canvas = FigureCanvasQTAgg(self.figure); self.canvas.setMinimumHeight(200); self.axes = self.figure.add_subplot(111)
        pl.addWidget(NavigationToolbar2QT(self.canvas, self)); pl.addWidget(self.canvas, 1)
        plot_options = QHBoxLayout(); self.show_all = QCheckBox('Show all thresholds (1, 2, 5, 10 km)'); self.show_all.toggled.connect(self.draw_plot)
        plot_options.addWidget(self.show_all); plot_options.addStretch()
        self.save_plot = QPushButton('Save plot'); self.save_plot.clicked.connect(self.export_plot); plot_options.addWidget(self.save_plot); pl.addLayout(plot_options)
        self.horizon_table = table(['Threshold', 'First saved exceedance', 'Crossing bracket (h)', 'Linear estimate (h)', 'Observation status'])
        self.tabs.addTab(plot_page, 'Daily error')
        threshold_page = QWidget(); th = QVBoxLayout(threshold_page)
        self.threshold_caption = label('No case selected'); th.addWidget(self.threshold_caption)
        th.addWidget(self.horizon_table)
        th.addWidget(label('Not reached means right-censored at the observation-window end. The first saved exceedance, neighboring-sample bracket and approximate linear estimate are distinct quantities.'))
        self.tabs.addTab(threshold_page, 'Threshold results')
        overview_page = QWidget(); ov = QVBoxLayout(overview_page)
        self.overview_label = label(''); ov.addWidget(self.overview_label)
        self.overview_table = table(['Satellite', 'Cases', 'Median daily RMS (km)', 'Median first 1 km crossing (h)', 'Flagged cases'])
        ov.addWidget(self.overview_table); ov.addWidget(label('Descriptive summaries only. Related satellites and overlapping windows are not independent trials. The median crossing is shown only when every included case crosses 1 km.'))
        self.tabs.addTab(overview_page, 'Study overview')
        self.details = QTextEdit(); self.details.setReadOnly(True); self.tabs.addTab(self.details, 'Inputs and provenance')
        self.notes = QTextEdit(); self.notes.setReadOnly(True)
        self.notes.setPlainText('How to explain this demonstration\n\n1. Select a satellite and TLE epoch. The original saved daily results are displayed; this viewer does not regenerate predictions.\n\n2. The blue curve is the three-dimensional position difference between SGP4 and the precise reference, expressed in geocentric ITRS, in kilometres. Time is elapsed hours from the historical TLE epoch.\n\n3. A first exceedance is the first saved sample strictly above a threshold. The error can later return below it. The linear estimate is approximate; minute samples cannot establish continuous safety.\n\n4. Not reached means right-censored at the observed window end. It does not mean the horizon is exactly 24 hours or unlimited.\n\n5. POE (Sentinel) and PSO (Swarm) are distinct reference products. They are not independent measurements of absolute truth. Earth-fixed realization differences remain unmodelled.\n\n6. Reference flags come from the supplied source audit. Excluding every case using a flagged bracket is a conservative sensitivity analysis chosen after inspecting the data. Unknown quality annotations are excluded by that filter as well.\n\n7. This is a retrospective study. TLE epoch alone does not establish public availability or prospective forecasting performance. A separate prospective capture must wait for reference validation.\n\nThe source case files remain read-only. File-hash checks establish consistency with saved summaries, not an external signature or certification of orbit accuracy.')
        self.tabs.addTab(self.notes, 'Defense guide')
        bottom = QHBoxLayout(); bottom.addWidget(label('Historical study  •  First crossing can be followed by recovery.'), 1)
        self.export_button = QPushButton('Export selected case'); self.export_button.clicked.connect(self.export_case); bottom.addWidget(self.export_button); layout.addLayout(bottom)
        self.setCentralWidget(root)
        self.satellite_combo.currentIndexChanged.connect(self.update_cases)
        self.case_combo.currentIndexChanged.connect(self.update_case)
        self.screened.toggled.connect(self.update_cases)
        self.clear_case()
        if results is None:
            runtime = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
            bundled = runtime/'defense_demo'/'may_results.zip'
            if bundled.exists():
                results = bundled
        if results:
            self.load_results(results)

    def clear_case(self):
        self.current_case = self.current_series = None
        self.earth_view.set_series(None,None)
        self.metrics.setText('No case selected'); self.details.clear(); self.horizon_table.setRowCount(0)
        self.threshold_caption.setText('No case selected')
        self.quality.setText('Reference-quality annotation unavailable.'); self.quality.setStyleSheet('background:#e8edf3; padding:9px;')
        self.export_button.setEnabled(False); self.save_plot.setEnabled(False); self.draw_plot()

    def choose_zip(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open historical results ZIP', '', 'ZIP archives (*.zip)')
        if path: self.load_results(path)

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Select the folder containing saved case directories')
        if path: self.load_results(path)

    def load_results(self, path, show_errors=True):
        try:
            archive = ResultArchive(path)
        except Exception as exc:
            self.last_error = str(exc)
            if show_errors: QMessageBox.warning(self, 'Cannot open results', str(exc))
            return False
        self.archive = archive; self.last_error = None
        self.satellite_combo.blockSignals(True); self.satellite_combo.clear()
        for key, (name, _, _) in SATELLITES.items():
            if archive.filtered(key): self.satellite_combo.addItem(name, key)
        self.satellite_combo.blockSignals(False)
        days = sorted(c.date for c in archive.cases)
        self.dataset_label.setText(f'{len(archive.cases)} saved cases  •  TLE dates {days[0]} to {days[-1]}  •  {archive.path.name}')
        self.update_cases()
        return True

    def update_cases(self, *_):
        if not self.archive: return
        old = self.case_combo.currentData()
        old_id = old.case_id if old else None
        self.case_combo.blockSignals(True); self.case_combo.clear()
        cases = self.archive.filtered(self.satellite_combo.currentData(), self.screened.isChecked())
        for case in cases:
            text = case.summary['tle_epoch_utc'].replace('T', ' ').split('.')[0] + ' UTC'
            self.case_combo.addItem(text, case)
            if case.case_id == old_id: self.case_combo.setCurrentIndex(self.case_combo.count()-1)
        self.case_combo.blockSignals(False)
        screened = self.screened.isChecked()
        count = len(self.archive.filtered(screened=screened))
        self.overview_label.setText(f'{"Quality-screened sensitivity sample" if screened else "Full historical sample"}: {count} cases. Values are summaries of saved daily cases.')
        fill(self.overview_table, [(name, n, f'{rms:.6f}', 'Censored cases present' if first is None else f'{first:.6f}', flagged) for name,n,rms,first,flagged in self.archive.overview(screened)])
        self.update_case()

    def update_case(self, *_):
        self.clear_case()
        if not self.archive: return
        case = self.case_combo.currentData()
        if case is None:
            self.quality.setText('No cases meet this filter. Unknown annotations are excluded as well as flagged cases.'); return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            series = self.archive.series(case)
        except Exception as exc:
            self.last_error = str(exc); self.quality.setText('Cannot display case: '+str(exc)); self.statusBar().showMessage('Case validation failed; plot cleared.'); return
        finally:
            QApplication.restoreOverrideCursor()
        self.last_error = None; self.current_case = case; self.current_series = series; s = case.summary
        self.earth_view.set_series(case,series)
        self.metrics.setText(f'{SATELLITES[case.satellite][0]}   |   RMS {s["rms_error_km"]:.3f} km   |   Maximum {s["maximum_error_km"]:.3f} km   |   {s["sample_count"]:,} samples / {s["duration_hours"]:g} h')
        self.quality.setText(case.quality_label)
        shade = '#fff0d6' if case.flagged_samples else '#e8f2ed' if case.flagged_samples == 0 else '#e8edf3'
        self.quality.setStyleSheet(f'background:{shade}; padding:9px; border-radius:6px;')
        rows = []
        for h in s['horizons']:
            censored = h['right_censored']; first = h['first_exceedance_hours']
            rows.append((f'{h["threshold_km"]:g} km', 'Not reached' if censored else f'{first:.6f} h',
                         '—' if censored else f'{h["bracket_start_hours"]:.6f}–{h["bracket_end_hours"]:.6f}',
                         '—' if censored else f'{h["horizon_hours_estimate"]:.6f}',
                         f'Right-censored at {h["observed_duration_hours"]:g} h' if censored else 'Above threshold at origin' if first == 0 else 'First sampled exceedance'))
        fill(self.horizon_table, rows)
        self.threshold_caption.setText(f'{s["satellite_name"]} · TLE epoch {s["tle_epoch_utc"]} · {s["duration_hours"]:g} h observation window')
        refs = '\n\n'.join(f'{r["filename"]}\nSHA-256: {r["sha256"]}\nUTC validity: {r["validity_start_utc"]} to {r["validity_stop_utc"]}' for r in s['references'])
        detail = (f'Case: {case.case_id}\nSatellite: {s["satellite_name"]} / NORAD {s["norad_id"]}\nMode: retrospective\nTLE epoch (UTC): {s["tle_epoch_utc"]}\nEvaluation end (UTC): {s["evaluation_end_utc"]}\nReference: {s["reference_grade"]} / {s["reference_product"]}\nPrediction: SGP4 / TEME → geocentric ITRS\nReference interpolation: {s["reference_interpolation"]}\n\n{case.quality_label}\nQuality screening: whole-case exclusion if any used reference bracket is non-NOMINAL; annotations unavailable for unreviewed archives.\n\nSaved errors.csv, horizons.csv and evaluated_tle.tle hashes checked; sampled error norms, RMS and first crossings checked for this displayed case.\nSummary SHA-256: {case.summary_sha256}\n\nEvaluated TLE\n{series.tle}\nReference products\n{refs}\n\nArchived processing metadata\n'+json.dumps({k:s[k] for k in ('frame_transformation','package_versions','horizon_definition','limitations') if k in s},indent=2))
        self.details.setPlainText(detail); self.export_button.setEnabled(True); self.save_plot.setEnabled(True)
        self.statusBar().showMessage(f'Saved case checked and loaded • {case.date} • Offline')
        self.draw_plot()

    def draw_plot(self, *_):
        ax = self.axes; ax.clear(); ax.set_facecolor('white')
        ax.set_xlabel('Elapsed time from historical TLE epoch (hours)'); ax.set_ylabel('3D position error (km)')
        ax.grid(True, color='#e0e7ef', linewidth=.6)
        if self.current_series is not None:
            case = self.current_case; series = self.current_series
            ax.plot(series.hours, series.errors, color='#12669b', linewidth=1.6, label='Saved SGP4 / precise-reference error')
            maximum = max(series.errors)
            thresholds = [1,2,5,10] if self.show_all.isChecked() else [t for t in (1,2,5,10) if t <= max(2, maximum*1.12)]
            for t in thresholds: ax.axhline(t, color=COLORS[t], linestyle='--', linewidth=.9, label=f'{t} km threshold')
            first = next(h for h in case.summary['horizons'] if h['threshold_km']==1)['first_exceedance_hours']
            if first is not None:
                ax.axvline(first, color=COLORS[1], alpha=.6, linewidth=.9)
                ax.annotate(f'First saved >1 km: {first:.2f} h', xy=(first,1), xytext=(.02,.95), textcoords='axes fraction', fontsize=9, va='top', color=COLORS[1])
            ax.set_xlim(0,series.hours[-1]); ax.set_ylim(0,max(maximum,max(thresholds))*1.18)
            ax.set_title(f'{SATELLITES[case.satellite][0]} · {case.date} UTC · {case.summary["reference_grade"]}',loc='left',fontsize=12,pad=12)
            ax.legend(loc='upper right', fontsize=8)
        else:
            ax.text(.5,.5,'Select a saved historical case',transform=ax.transAxes,ha='center',va='center',color='#657a90')
        self.canvas.draw_idle()

    def closeEvent(self, event):
        self.earth_view.pause()
        super().closeEvent(event)

    def export_plot(self):
        if self.current_case is None: return
        path, _ = QFileDialog.getSaveFileName(self, 'Save current plot', self.current_case.case_id+'.png', 'PNG image (*.png)')
        if path:
            try: self.figure.savefig(path, dpi=180); self.statusBar().showMessage('Plot saved: '+path)
            except Exception as exc: QMessageBox.warning(self,'Cannot save plot',str(exc))

    def export_case(self):
        if self.current_case is None: return
        path = QFileDialog.getExistingDirectory(self, 'Choose a parent folder for the new case export')
        if path:
            try:
                destination = self.archive.export_case(self.current_case,path)
                self.statusBar().showMessage('Case exported: '+str(destination))
            except Exception as exc: QMessageBox.warning(self,'Cannot export case',str(exc))
