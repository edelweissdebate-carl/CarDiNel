from __future__ import annotations

import argparse
import csv
from pathlib import Path

from src.core.batch_analyzer import (
    analyze_one_tle,
    find_tle_catalog,
    load_all_reference_states,
)
from src.core.paths import (
    RESULTS_DIR,
    ensure_app_directories,
    get_reference_dir,
)
from src.core.satellites import (
    DEFAULT_SATELLITE_KEY,
    SATELLITES,
    get_satellite,
)
from src.io.tle_reader import load_tle_catalog


# ---------------------------------------------------------------------
# Output path
# ---------------------------------------------------------------------

def get_timeseries_output_file(
    satellite_key: str,
) -> Path:
    """
    Return the detailed position-error time-series CSV path
    for one supported satellite.
    """

    satellite = get_satellite(
        satellite_key
    )

    return (
        RESULTS_DIR
        / f"{satellite.key}_error_timeseries.csv"
    )


# ---------------------------------------------------------------------
# Time-series export
# ---------------------------------------------------------------------

def export_timeseries(
    satellite_key: str,
    tle_file: Path | None = None,
) -> Path:
    """
    Run each unique historical TLE through the ORBIT-TRUST
    comparison pipeline and export every matched error observation.

    Each row represents one timestamp within one TLE's
    24-hour propagation window.
    """

    ensure_app_directories()

    satellite = get_satellite(
        satellite_key
    )

    tle_catalog_file = find_tle_catalog(
        satellite_key=satellite.key,
        requested_file=tle_file,
    )

    reference_folder = get_reference_dir(
        satellite.key
    )

    output_file = get_timeseries_output_file(
        satellite.key
    )

    print("=" * 70)
    print(
        "ORBIT-TRUST Detailed Error Time-Series Export"
    )
    print("=" * 70)

    print(
        f"Satellite:        {satellite.name}"
    )

    print(
        f"Mission code:     {satellite.short_name}"
    )

    print(
        f"NORAD catalog:    {satellite.norad_id}"
    )

    print(
        f"TLE catalog:      {tle_catalog_file}"
    )

    print(
        f"Reference folder: {reference_folder}"
    )

    print(
        f"Output CSV:       {output_file}"
    )

    print()

    # -------------------------------------------------------------
    # Load unique TLEs.
    # -------------------------------------------------------------

    tle_entries = load_tle_catalog(
        tle_catalog_file
    )

    # -------------------------------------------------------------
    # Load precise-orbit reference trajectory.
    # -------------------------------------------------------------

    reference_states = (
        load_all_reference_states(
            reference_folder
        )
    )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "satellite_name",
        "mission_code",
        "norad_id",
        "tle_name",
        "tle_epoch_utc",
        "observation_time_utc",
        "observation_index",
        "elapsed_seconds",
        "elapsed_minutes",
        "elapsed_hours",
        "total_error_km",
        "radial_error_km",
        "along_track_error_km",
        "cross_track_error_km",
    ]

    successful_tles = 0
    skipped_tles = 0
    total_rows = 0

    with output_file.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as csv_file:

        writer = csv.DictWriter(
            csv_file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for number, tle in enumerate(
            tle_entries,
            start=1,
        ):
            print(
                f"[{number}/{len(tle_entries)}] "
                f"Processing {satellite.name} "
                f"TLE epoch "
                f"{tle.epoch.isoformat()}..."
            )

            result = analyze_one_tle(
                satellite=satellite,
                tle=tle,
                all_reference_states=(
                    reference_states
                ),
            )

            if result is None:
                skipped_tles += 1

                print(
                    "  Skipped."
                )
                print()

                continue

            errors = (
                result.comparison.errors
            )

            if not errors:
                skipped_tles += 1

                print(
                    "  No valid error observations."
                )
                print()

                continue

            # -----------------------------------------------------
            # Export every matched observation.
            #
            # IMPORTANT:
            # elapsed time is measured from the actual TLE epoch,
            # not merely from the first available reference state.
            # -----------------------------------------------------

            rows_for_tle = 0

            for observation_index, error in enumerate(
                errors,
                start=1,
            ):
                elapsed_seconds = (
                    error.time
                    - tle.epoch
                ).total_seconds()

                # Ignore any state that somehow falls before epoch.
                if elapsed_seconds < 0:
                    continue

                writer.writerow(
                    {
                        "satellite_name": (
                            satellite.name
                        ),
                        "mission_code": (
                            satellite.short_name
                        ),
                        "norad_id": (
                            satellite.norad_id
                        ),
                        "tle_name": (
                            tle.name
                        ),
                        "tle_epoch_utc": (
                            tle.epoch.isoformat()
                        ),
                        "observation_time_utc": (
                            error.time.isoformat()
                        ),
                        "observation_index": (
                            observation_index
                        ),
                        "elapsed_seconds": (
                            elapsed_seconds
                        ),
                        "elapsed_minutes": (
                            elapsed_seconds / 60.0
                        ),
                        "elapsed_hours": (
                            elapsed_seconds / 3600.0
                        ),
                        "total_error_km": (
                            error.total_error_km
                        ),
                        "radial_error_km": (
                            error.radial_error_km
                        ),
                        "along_track_error_km": (
                            error.along_track_error_km
                        ),
                        "cross_track_error_km": (
                            error.cross_track_error_km
                        ),
                    }
                )

                rows_for_tle += 1
                total_rows += 1

            successful_tles += 1

            print(
                f"  Exported observations: "
                f"{rows_for_tle}"
            )

            print()

    if successful_tles == 0:
        raise RuntimeError(
            "No TLEs produced valid time-series data."
        )

    print("=" * 70)

    print(
        f"Completed TLEs: "
        f"{successful_tles}"
    )

    print(
        f"Skipped TLEs: "
        f"{skipped_tles}"
    )

    print(
        f"Total time-series rows: "
        f"{total_rows}"
    )

    print(
        f"Saved to:\n"
        f"{output_file}"
    )

    print("=" * 70)

    return output_file


# ---------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export detailed ORBIT-TRUST "
            "position-error time-series data."
        )
    )

    parser.add_argument(
        "--satellite",
        choices=sorted(
            SATELLITES.keys()
        ),
        default=DEFAULT_SATELLITE_KEY,
        help=(
            "Satellite to process. "
            f"Default: "
            f"{DEFAULT_SATELLITE_KEY}"
        ),
    )

    parser.add_argument(
        "--tle-file",
        type=Path,
        default=None,
        help=(
            "Optional historical TLE catalog. "
            "If omitted, the newest catalog "
            "for the selected satellite is used."
        ),
    )

    return parser.parse_args()


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------

def main() -> None:
    args = parse_arguments()

    export_timeseries(
        satellite_key=args.satellite,
        tle_file=args.tle_file,
    )


if __name__ == "__main__":
    main()