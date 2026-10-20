from __future__ import annotations

import os
import sys
from pathlib import Path

from src.core.satellites import SATELLITES


APP_NAME = "ORBIT-TRUST"


def get_runtime_root() -> Path:
    """
    Return the project root while running from source.

    If ORBIT-TRUST is frozen into an executable, return the
    folder containing the executable.
    """

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parents[2]


def get_user_data_root() -> Path:
    """
    Return a writable user-data directory for packaged builds.
    """

    # Windows
    local_app_data = os.getenv("LOCALAPPDATA")

    if local_app_data:
        return Path(local_app_data) / APP_NAME

    # macOS
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / APP_NAME
        )

    # Linux / other Unix systems
    xdg_data_home = os.getenv("XDG_DATA_HOME")

    if xdg_data_home:
        return Path(xdg_data_home) / APP_NAME

    return (
        Path.home()
        / ".local"
        / "share"
        / APP_NAME
    )


RUNTIME_ROOT = get_runtime_root()

USER_DATA_ROOT = get_user_data_root()


def get_working_root() -> Path:
    """
    Return the location where ORBIT-TRUST should read/write
    research data.

    Source-code development:
        use the repository folder.

    Frozen executable:
        use the operating system's writable application-data
        directory.
    """

    if getattr(sys, "frozen", False):
        return USER_DATA_ROOT

    return RUNTIME_ROOT


WORKING_ROOT = get_working_root()


# -------------------------------------------------------------------
# Main folders
# -------------------------------------------------------------------

DATA_DIR = WORKING_ROOT / "data"

RESULTS_DIR = WORKING_ROOT / "results"

STATISTICS_DIR = RESULTS_DIR / "statistics"

ENV_FILE = WORKING_ROOT / ".env"


# -------------------------------------------------------------------
# Satellite-specific folders
# -------------------------------------------------------------------

def get_satellite_data_dir(
    satellite_key: str,
) -> Path:
    """
    Return the main data folder for one satellite.

    Example:
        data/sentinel1a
        data/sentinel1b
    """

    normalized = satellite_key.strip().lower()

    if normalized not in SATELLITES:
        raise ValueError(
            f"Unsupported satellite: {satellite_key}"
        )

    return DATA_DIR / normalized


def get_tle_dir(
    satellite_key: str,
) -> Path:
    """
    Return the historical TLE folder for one satellite.
    """

    return (
        get_satellite_data_dir(satellite_key)
        / "tle"
    )


def get_reference_dir(
    satellite_key: str,
) -> Path:
    """
    Return the precise orbit reference folder for one satellite.
    """

    return (
        get_satellite_data_dir(satellite_key)
        / "reference"
    )


def get_batch_result_file(
    satellite_key: str,
) -> Path:
    """
    Return the batch reliability CSV path for one satellite.
    """

    normalized = satellite_key.strip().lower()

    return (
        RESULTS_DIR
        / f"{normalized}_batch_reliability.csv"
    )


def get_satellite_statistics_dir(
    satellite_key: str,
) -> Path:
    """
    Return a statistics folder dedicated to one satellite.
    """

    normalized = satellite_key.strip().lower()

    return (
        STATISTICS_DIR
        / normalized
    )


# -------------------------------------------------------------------
# Directory initialization
# -------------------------------------------------------------------

def ensure_app_directories() -> None:
    """
    Create all folders required by ORBIT-TRUST.
    """

    base_folders = (
        WORKING_ROOT,
        DATA_DIR,
        RESULTS_DIR,
        STATISTICS_DIR,
    )

    for folder in base_folders:
        folder.mkdir(
            parents=True,
            exist_ok=True,
        )

    for satellite_key in SATELLITES:
        get_tle_dir(
            satellite_key
        ).mkdir(
            parents=True,
            exist_ok=True,
        )

        get_reference_dir(
            satellite_key
        ).mkdir(
            parents=True,
            exist_ok=True,
        )

        get_satellite_statistics_dir(
            satellite_key
        ).mkdir(
            parents=True,
            exist_ok=True,
        )