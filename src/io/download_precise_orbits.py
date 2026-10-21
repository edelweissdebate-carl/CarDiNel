from __future__ import annotations

import argparse
from datetime import (
    date,
    datetime,
    time,
    timezone,
)
from pathlib import Path

from src.core.paths import (
    ensure_app_directories,
    get_reference_dir,
)
from src.core.satellites import (
    DEFAULT_SATELLITE_KEY,
    SATELLITES,
    get_satellite,
)
from src.io.sentinel_orbit_downloader import (
    SentinelOrbitDownloader,
)


# ---------------------------------------------------------------------
# Default research settings
# ---------------------------------------------------------------------

DEFAULT_START_DATE = date(2021, 10, 1)
DEFAULT_END_DATE = date(2021, 10, 31)


# ---------------------------------------------------------------------
# Date conversion
# ---------------------------------------------------------------------

def date_range_to_datetimes(
    start_date: date,
    end_date: date,
) -> tuple[datetime, datetime]:
    """
    Convert inclusive calendar dates into timezone-aware UTC
    datetime boundaries.

    Example:
        2021-10-01
        through
        2021-10-03

    becomes:

        2021-10-01 00:00:00 UTC
        through
        2021-10-03 23:59:59.999999 UTC
    """

    if end_date < start_date:
        raise ValueError(
            "End date cannot be earlier than start date."
        )

    start_time = datetime.combine(
        start_date,
        time.min,
        tzinfo=timezone.utc,
    )

    stop_time = datetime.combine(
        end_date,
        time.max,
        tzinfo=timezone.utc,
    )

    return start_time, stop_time


# ---------------------------------------------------------------------
# Precise-orbit download
# ---------------------------------------------------------------------

def download_precise_orbits(
    satellite_key: str,
    start_date: date,
    end_date: date,
) -> list[Path]:
    """
    Discover and download Sentinel AUX_POEORB products for one
    supported satellite and date range.

    Existing files are automatically reused by the underlying
    downloader.
    """

    ensure_app_directories()

    satellite = get_satellite(
        satellite_key
    )

    if satellite.reference_provider == "swarm_sp3":
        from src.io.swarm_orbits import download_swarm_orbits
        return download_swarm_orbits(satellite.key, start_date, end_date, get_reference_dir(satellite.key))

    start_time, stop_time = (
        date_range_to_datetimes(
            start_date=start_date,
            end_date=end_date,
        )
    )

    output_folder = get_reference_dir(
        satellite.key
    )

    print("=" * 70)
    print("ORBIT-TRUST Precise Orbit Downloader")
    print("=" * 70)

    print(
        f"Satellite:       "
        f"{satellite.name}"
    )

    print(
        f"Mission code:    "
        f"{satellite.short_name}"
    )

    print(
        f"NORAD catalog:   "
        f"{satellite.norad_id}"
    )

    print(
        f"Start time:      "
        f"{start_time.isoformat()}"
    )

    print(
        f"Stop time:       "
        f"{stop_time.isoformat()}"
    )

    print(
        f"Output folder:   "
        f"{output_folder}"
    )

    print()
    print(
        f"Searching for {satellite.name} "
        "AUX_POEORB files..."
    )

    downloader = SentinelOrbitDownloader()

    matching_files = downloader.list_matching_files(
        satellite=satellite.short_name,
        start_time=start_time,
        stop_time=stop_time,
    )

    if not matching_files:
        raise RuntimeError(
            f"No matching {satellite.name} AUX_POEORB "
            "files were found for the requested period."
        )

    print()
    print(
        f"Matching files found: "
        f"{len(matching_files)}"
    )

    print()

    for number, file_info in enumerate(
        matching_files,
        start=1,
    ):
        print(
            f"[{number}/{len(matching_files)}] "
            f"{file_info.validity_start.isoformat()} "
            f"to "
            f"{file_info.validity_stop.isoformat()}"
        )

        print(
            f"    {file_info.filename}"
        )

    print()
    print(
        f"Preparing {satellite.name} "
        "precise orbit files..."
    )
    print()

    downloaded = downloader.download_files(
        files=matching_files,
        output_folder=output_folder,
    )

    print()
    print("=" * 70)

    print(
        f"Precise-orbit acquisition complete: "
        f"{satellite.name}"
    )

    print(
        f"Files ready: "
        f"{len(downloaded)}"
    )

    print(
        f"Saved in:\n{output_folder}"
    )

    print("=" * 70)

    return downloaded


# ---------------------------------------------------------------------
# Command-line arguments
# ---------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download Sentinel POE or Swarm precise science orbit "
            "products from their public archives."
        )
    )

    parser.add_argument(
        "--satellite",
        choices=sorted(SATELLITES.keys()),
        default=DEFAULT_SATELLITE_KEY,
        help=(
            "Satellite to download. "
            f"Default: {DEFAULT_SATELLITE_KEY}"
        ),
    )

    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=DEFAULT_START_DATE,
        help=(
            "Start date in YYYY-MM-DD format. "
            f"Default: "
            f"{DEFAULT_START_DATE.isoformat()}"
        ),
    )

    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=DEFAULT_END_DATE,
        help=(
            "End date in YYYY-MM-DD format. "
            f"Default: "
            f"{DEFAULT_END_DATE.isoformat()}"
        ),
    )

    return parser.parse_args()


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------

def main() -> None:
    args = parse_arguments()

    download_precise_orbits(
        satellite_key=args.satellite,
        start_date=args.start,
        end_date=args.end,
    )


if __name__ == "__main__":
    main()