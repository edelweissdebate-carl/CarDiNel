from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src.core.paths import (
    ENV_FILE,
    ensure_app_directories,
    get_tle_dir,
)
from src.core.satellites import (
    DEFAULT_SATELLITE_KEY,
    SATELLITES,
    get_satellite,
)
from src.io.spacetrack_client import (
    SpaceTrackClient,
    TLEDownloadRequest,
)


# ---------------------------------------------------------------------
# Default research settings
# ---------------------------------------------------------------------

DEFAULT_START_DATE = date(2021, 10, 1)
DEFAULT_END_DATE = date(2021, 10, 31)


# ---------------------------------------------------------------------
# Output path
# ---------------------------------------------------------------------

def build_output_file(
    satellite_key: str,
    start_date: date,
    end_date: date,
) -> Path:
    """
    Build the historical TLE output path for a supported satellite.

    Example:
        data/sentinel1a/tle/
        sentinel1a_batch_2021-10-01_to_2021-10-31.tle
    """

    satellite = get_satellite(satellite_key)

    output_directory = get_tle_dir(
        satellite.key
    )

    filename = (
        f"{satellite.key}_batch_"
        f"{start_date.isoformat()}_to_"
        f"{end_date.isoformat()}.tle"
    )

    return output_directory / filename


# ---------------------------------------------------------------------
# Historical TLE download
# ---------------------------------------------------------------------

def download_tles(
    satellite_key: str,
    start_date: date,
    end_date: date,
    force_download: bool = False,
) -> Path:
    """
    Download historical TLEs from Space-Track for one satellite.

    Existing files are reused unless force_download=True.
    """

    ensure_app_directories()

    satellite = get_satellite(
        satellite_key
    )

    output_file = build_output_file(
        satellite_key=satellite.key,
        start_date=start_date,
        end_date=end_date,
    )

    # -------------------------------------------------------------
    # Reuse previously downloaded historical data when possible.
    # -------------------------------------------------------------

    if (
        output_file.exists()
        and output_file.stat().st_size > 0
        and not force_download
    ):
        print(
            f"Historical TLE file already exists "
            f"for {satellite.name}."
        )
        print(
            f"Using cached file:\n{output_file}"
        )

        return output_file

    # -------------------------------------------------------------
    # Load Space-Track credentials.
    # -------------------------------------------------------------

    load_dotenv(
        ENV_FILE,
        override=False,
    )

    username = os.getenv(
        "SPACETRACK_USERNAME",
        "",
    )

    password = os.getenv(
        "SPACETRACK_PASSWORD",
        "",
    )

    if not username.strip():
        raise RuntimeError(
            "Space-Track username was not found.\n"
            f"Expected credentials file:\n{ENV_FILE}\n\n"
            "Required variable:\n"
            "SPACETRACK_USERNAME"
        )

    if not password:
        raise RuntimeError(
            "Space-Track password was not found.\n"
            f"Expected credentials file:\n{ENV_FILE}\n\n"
            "Required variable:\n"
            "SPACETRACK_PASSWORD"
        )

    # -------------------------------------------------------------
    # Prepare Space-Track request.
    # -------------------------------------------------------------

    request = TLEDownloadRequest(
        norad_catalog_id=satellite.norad_id,
        start_date=start_date,
        end_date=end_date,
        output_file=output_file,
    )

    print("=" * 70)
    print("ORBIT-TRUST Historical TLE Downloader")
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
        f"Start date:      "
        f"{start_date.isoformat()}"
    )

    print(
        f"End date:        "
        f"{end_date.isoformat()}"
    )

    print(
        f"Output file:     "
        f"{output_file}"
    )

    print()
    print(
        "Logging in to Space-Track..."
    )

    # -------------------------------------------------------------
    # Download.
    # -------------------------------------------------------------

    with SpaceTrackClient(
        username=username,
        password=password,
    ) as client:

        client.login()

        print(
            "Space-Track login successful."
        )

        print(
            f"Downloading historical TLEs "
            f"for {satellite.name}..."
        )

        tle_count = (
            client.download_historical_tles(
                request
            )
        )

    print()
    print("=" * 70)

    print(
        f"Download complete: "
        f"{satellite.name}"
    )

    print(
        f"TLE pairs downloaded: "
        f"{tle_count}"
    )

    print(
        f"Saved to:\n{output_file}"
    )

    print("=" * 70)

    return output_file


# ---------------------------------------------------------------------
# Command-line arguments
# ---------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    """
    Parse command-line arguments.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Download historical TLE data "
            "for an ORBIT-TRUST satellite."
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

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Download again even when the "
            "requested historical TLE file "
            "already exists locally."
        ),
    )

    return parser.parse_args()


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------

def main() -> None:
    args = parse_arguments()

    download_tles(
        satellite_key=args.satellite,
        start_date=args.start,
        end_date=args.end,
        force_download=args.force,
    )


if __name__ == "__main__":
    main()