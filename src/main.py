from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path


# ---------------------------------------------------------------------
# Project import path
# ---------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )


from src.core.paths import ensure_app_directories


# ---------------------------------------------------------------------
# Background research-worker mode
# ---------------------------------------------------------------------

def run_pipeline_worker(
    argv: list[str],
) -> int:
    """
    Run the ORBIT-TRUST research pipeline without creating the GUI.

    The GUI starts this mode in a separate operating-system process.
    That isolates CPU-heavy Astropy/SGP4/statistics work from Qt's
    main event loop, so the application remains responsive while the
    experiment is running.

    This same entry point is also compatible with a future PyInstaller
    executable: the frozen ORBIT-TRUST executable can launch itself
    with --pipeline-worker instead of trying to launch Python with -m.
    """

    parser = argparse.ArgumentParser(
        prog="ORBIT-TRUST pipeline worker",
    )

    parser.add_argument(
        "--satellite",
        choices=[
            "sentinel1a",
            "sentinel1b",
            "both",
        ],
        required=True,
    )

    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        required=True,
    )

    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        required=True,
    )

    parser.add_argument(
        "--alpha",
        type=float,
        default=0.05,
    )

    parser.add_argument(
        "--force-tles",
        action="store_true",
    )

    args = parser.parse_args(
        argv
    )

    if args.end < args.start:
        raise ValueError(
            "End date cannot be earlier than start date."
        )

    if not (
        0.0
        < args.alpha
        < 1.0
    ):
        raise ValueError(
            "Alpha must be between 0 and 1."
        )

    ensure_app_directories()

    # The worker process is connected to QProcess pipes on Windows.
    # Without an explicit UTF-8 encoding, Python may fall back to the
    # Windows cp1252 code page and crash when pipeline messages contain
    # characters such as arrows, em dashes, or check marks.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(
            stream,
            "reconfigure",
            None,
        )

        if callable(reconfigure):
            reconfigure(
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
            )

    # Import only in worker mode so the GUI process does not need to
    # execute the scientific pipeline itself.
    from src.core.research_pipeline import (
        run_research_pipeline,
    )

    run_research_pipeline(
        scope=args.satellite,
        start_date=args.start,
        end_date=args.end,
        alpha=args.alpha,
        force_tle_download=(
            args.force_tles
        ),
    )

    return 0


# ---------------------------------------------------------------------
# GUI mode
# ---------------------------------------------------------------------

def run_gui() -> int:
    ensure_app_directories()

    from PySide6.QtWidgets import QApplication
    from src.gui.main_window import MainWindow

    app = QApplication(
        sys.argv
    )

    app.setApplicationName(
        "ORBIT-TRUST"
    )

    app.setOrganizationName(
        "Bayugan National Comprehensive High School"
    )

    window = MainWindow()
    window.show()

    return app.exec()


# ---------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------

def main() -> int:
    if (
        "--pipeline-worker"
        in sys.argv
    ):
        marker_index = (
            sys.argv.index(
                "--pipeline-worker"
            )
        )

        worker_arguments = (
            sys.argv[
                marker_index + 1:
            ]
        )

        return run_pipeline_worker(
            worker_arguments
        )

    return run_gui()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )