"""Launch the offline study viewer without loading legacy analysis tabs."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description='CarDiNel offline historical-results viewer')
    parser.add_argument('--results', type=Path, help='Results ZIP or extracted batch folder')
    args = parser.parse_args(argv)
    from PySide6.QtWidgets import QApplication
    from src.gui.defense_window import DefenseWindow
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('CarDiNel Defense Viewer')
    window = DefenseWindow(args.results)
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
