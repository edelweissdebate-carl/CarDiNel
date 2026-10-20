from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sgp4.api import Satrec, jday

from src.core.comparator import (
    ComparisonResult,
    OrbitRecord,
    compare_orbits,
)
from src.core.paths import (
    ensure_app_directories,
    get_batch_result_file,
    get_reference_dir,
    get_tle_dir,
)
from src.core.reliability import (
    ReliabilityResult,
    calculate_reliability_horizon,
)
from src.core.satellites import (
    DEFAULT_SATELLITE_KEY,
    SATELLITES,
    SatelliteConfig,
    get_satellite,
)
from src.io.eof_parser import (
    PreciseOrbitState,
    parse_eof_file,
)
from src.io.frame_converter import teme_to_itrs
from src.io.tle_reader import (
    TLEData,
    load_tle_catalog,
)


# ---------------------------------------------------------------------
# Research settings
# ---------------------------------------------------------------------

ERROR_THRESHOLDS_KM = (
    1.0,
    2.0,
    5.0,
    10.0,
)

SAMPLE_INTERVAL_SECONDS = 60

MAX_PROPAGATION_HOURS = 24.0

MINIMUM_REQUIRED_HOURS = 23.5


# ---------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------

@dataclass
class BatchResult:
    satellite: SatelliteConfig
    tle: TLEData
    comparison: ComparisonResult
    reliability: dict[float, ReliabilityResult]


# ---------------------------------------------------------------------
# Time conversion
# ---------------------------------------------------------------------

def datetime_to_julian(
    time: datetime,
) -> tuple[float, float]:
    """
    Convert a datetime into the two-part Julian date expected by SGP4.
    """

    return jday(
        time.year,
        time.month,
        time.day,
        time.hour,
        time.minute,
        time.second
        + time.microsecond / 1_000_000,
    )


# ---------------------------------------------------------------------
# TLE file discovery
# ---------------------------------------------------------------------

def find_tle_catalog(
    satellite_key: str,
    requested_file: Path | None = None,
) -> Path:
    """
    Determine which historical TLE catalog should be analyzed.

    If --tle-file is supplied, that exact file is used.

    Otherwise the newest .tle file inside the satellite's TLE
    directory is selected automatically.
    """

    satellite = get_satellite(
        satellite_key
    )

    if requested_file is not None:
        requested_file = requested_file.resolve()

        if not requested_file.exists():
            raise FileNotFoundError(
                "The requested historical TLE file "
                f"does not exist:\n{requested_file}"
            )

        return requested_file

    tle_directory = get_tle_dir(
        satellite.key
    )

    tle_files = sorted(
        tle_directory.glob("*.tle"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not tle_files:
        raise FileNotFoundError(
            f"No historical TLE files were found "
            f"for {satellite.name} in:\n"
            f"{tle_directory}"
        )

    return tle_files[0]


# ---------------------------------------------------------------------
# Precise orbit loading
# ---------------------------------------------------------------------

def load_all_reference_states(
    eof_folder: Path,
) -> list[PreciseOrbitState]:
    """
    Load all Sentinel AUX_POEORB EOF files in a folder.

    Duplicate timestamps resulting from overlapping precise-orbit
    products are removed.
    """

    eof_files = sorted(
        eof_folder.glob("*.EOF")
    )

    if not eof_files:
        raise FileNotFoundError(
            "No precise-orbit EOF files were found in:\n"
            f"{eof_folder}"
        )

    states_by_time: dict[
        datetime,
        PreciseOrbitState,
    ] = {}

    for eof_file in eof_files:
        print(
            f"Loading reference file: "
            f"{eof_file.name}"
        )

        states = parse_eof_file(
            eof_file
        )

        for state in states:
            states_by_time[
                state.time
            ] = state

    combined_states = sorted(
        states_by_time.values(),
        key=lambda state: state.time,
    )

    if not combined_states:
        raise RuntimeError(
            "No precise orbit states could be loaded."
        )

    print()
    print(
        f"EOF files loaded: "
        f"{len(eof_files)}"
    )

    print(
        f"Combined reference states: "
        f"{len(combined_states)}"
    )

    print(
        f"Reference start: "
        f"{combined_states[0].time.isoformat()}"
    )

    print(
        f"Reference end: "
        f"{combined_states[-1].time.isoformat()}"
    )

    print()

    return combined_states


# ---------------------------------------------------------------------
# Reference trajectory preparation
# ---------------------------------------------------------------------

def downsample_reference_states(
    states: list[PreciseOrbitState],
    minimum_interval_seconds: int,
) -> list[PreciseOrbitState]:
    """
    Keep precise orbit states separated by at least the requested
    temporal interval.
    """

    if not states:
        return []

    selected = [
        states[0]
    ]

    last_time = states[0].time

    for state in states[1:]:
        separation_seconds = (
            state.time - last_time
        ).total_seconds()

        if (
            separation_seconds
            >= minimum_interval_seconds
        ):
            selected.append(
                state
            )

            last_time = state.time

    return selected


def select_reference_window(
    states: list[PreciseOrbitState],
    start_time: datetime,
    duration_hours: float,
) -> list[PreciseOrbitState]:
    """
    Select reference states between the TLE epoch and the end of
    the requested propagation interval.
    """

    end_time = (
        start_time
        + timedelta(
            hours=duration_hours
        )
    )

    return [
        state
        for state in states
        if (
            start_time
            <= state.time
            <= end_time
        )
    ]


def reference_to_orbit_record(
    state: PreciseOrbitState,
) -> OrbitRecord:
    """
    Convert a precise-orbit state into the common OrbitRecord format.
    """

    return OrbitRecord(
        time=state.time,
        x_km=state.x_km,
        y_km=state.y_km,
        z_km=state.z_km,
        vx_km_s=state.vx_km_s,
        vy_km_s=state.vy_km_s,
        vz_km_s=state.vz_km_s,
    )


# ---------------------------------------------------------------------
# TLE propagation
# ---------------------------------------------------------------------

def propagate_tle_at_reference_times(
    tle: TLEData,
    reference_states: list[PreciseOrbitState],
) -> list[OrbitRecord]:
    """
    Propagate one TLE at the exact precise-orbit timestamps and
    transform SGP4 TEME output into the comparison frame.
    """

    satellite = Satrec.twoline2rv(
        tle.line_1,
        tle.line_2,
    )

    predicted_records: list[
        OrbitRecord
    ] = []

    for reference in reference_states:
        jd, fraction = datetime_to_julian(
            reference.time
        )

        (
            error_code,
            position,
            velocity,
        ) = satellite.sgp4(
            jd,
            fraction,
        )

        if error_code != 0:
            print(
                "  SGP4 skipped timestamp "
                f"{reference.time.isoformat()} "
                f"with error code {error_code}."
            )

            continue

        converted = teme_to_itrs(
            time=reference.time,
            position_km=(
                position[0],
                position[1],
                position[2],
            ),
            velocity_km_s=(
                velocity[0],
                velocity[1],
                velocity[2],
            ),
        )

        predicted_records.append(
            OrbitRecord(
                time=converted.time,
                x_km=converted.x_km,
                y_km=converted.y_km,
                z_km=converted.z_km,
                vx_km_s=converted.vx_km_s,
                vy_km_s=converted.vy_km_s,
                vz_km_s=converted.vz_km_s,
            )
        )

    return predicted_records


# ---------------------------------------------------------------------
# Component statistics
# ---------------------------------------------------------------------

def calculate_component_rms(
    errors,
    attribute_name: str,
) -> float:
    """
    Calculate RMS magnitude for one position-error component.
    """

    values = [
        float(
            getattr(
                error,
                attribute_name,
            )
        )
        for error in errors
    ]

    if not values:
        return float("nan")

    return math.sqrt(
        sum(
            value * value
            for value in values
        )
        / len(values)
    )


# ---------------------------------------------------------------------
# Individual TLE analysis
# ---------------------------------------------------------------------

def analyze_one_tle(
    satellite: SatelliteConfig,
    tle: TLEData,
    all_reference_states: list[
        PreciseOrbitState
    ],
) -> BatchResult | None:
    """
    Analyze one historical TLE over the fixed 24-hour experiment.
    """

    reference_window = (
        select_reference_window(
            states=all_reference_states,
            start_time=tle.epoch,
            duration_hours=(
                MAX_PROPAGATION_HOURS
            ),
        )
    )

    reference_window = (
        downsample_reference_states(
            states=reference_window,
            minimum_interval_seconds=(
                SAMPLE_INTERVAL_SECONDS
            ),
        )
    )

    if len(reference_window) < 2:
        print(
            f"Skipped {tle.epoch.isoformat()}: "
            "not enough matching reference data."
        )

        return None

    available_hours = (
        reference_window[-1].time
        - reference_window[0].time
    ).total_seconds() / 3600.0

    if (
        available_hours
        < MINIMUM_REQUIRED_HOURS
    ):
        print(
            f"Skipped {tle.epoch.isoformat()}: "
            f"only {available_hours:.2f} hours "
            "of precise-orbit coverage were available."
        )

        return None

    predicted_records = (
        propagate_tle_at_reference_times(
            tle=tle,
            reference_states=(
                reference_window
            ),
        )
    )

    reference_records = [
        reference_to_orbit_record(
            state
        )
        for state in reference_window
    ]

    if len(predicted_records) < 2:
        print(
            f"Skipped {tle.epoch.isoformat()}: "
            "SGP4 returned too few valid states."
        )

        return None

    comparison = compare_orbits(
        predicted_records=predicted_records,
        reference_records=reference_records,
    )

    reliability = {
        threshold: (
            calculate_reliability_horizon(
                errors=comparison.errors,
                threshold_km=threshold,
            )
        )
        for threshold
        in ERROR_THRESHOLDS_KM
    }

    return BatchResult(
        satellite=satellite,
        tle=tle,
        comparison=comparison,
        reliability=reliability,
    )


# ---------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------

def export_batch_results(
    results: list[BatchResult],
    output_path: Path,
) -> None:
    """
    Save all completed TLE analyses to CSV.
    """

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "satellite_name",
        "mission_code",
        "norad_id",
        "tle_name",
        "tle_epoch_utc",
        "matched_records",
        "observation_hours",
        "initial_error_km",
        "final_error_km",
        "mean_error_km",
        "maximum_error_km",
        "rms_error_km",
        "radial_rms_km",
        "along_track_rms_km",
        "cross_track_rms_km",
    ]

    for threshold in (
        ERROR_THRESHOLDS_KM
    ):
        label = (
            f"{threshold:g}"
            .replace(".", "_")
        )

        fieldnames.extend(
            [
                (
                    f"horizon_"
                    f"{label}km_hours"
                ),
                (
                    f"horizon_"
                    f"{label}km_status"
                ),
                (
                    f"crossed_"
                    f"{label}km"
                ),
            ]
        )

    with output_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as csv_file:

        writer = csv.DictWriter(
            csv_file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for result in results:
            errors = (
                result.comparison.errors
            )

            observation_hours = (
                errors[-1].time
                - errors[0].time
            ).total_seconds() / 3600.0

            radial_rms = (
                calculate_component_rms(
                    errors,
                    "radial_error_km",
                )
            )

            along_track_rms = (
                calculate_component_rms(
                    errors,
                    "along_track_error_km",
                )
            )

            cross_track_rms = (
                calculate_component_rms(
                    errors,
                    "cross_track_error_km",
                )
            )

            row = {
                "satellite_name": (
                    result.satellite.name
                ),
                "mission_code": (
                    result.satellite.short_name
                ),
                "norad_id": (
                    result.satellite.norad_id
                ),
                "tle_name": (
                    result.tle.name
                ),
                "tle_epoch_utc": (
                    result.tle.epoch.isoformat()
                ),
                "matched_records": (
                    len(errors)
                ),
                "observation_hours": (
                    observation_hours
                ),
                "initial_error_km": (
                    errors[0].total_error_km
                ),
                "final_error_km": (
                    errors[-1].total_error_km
                ),
                "mean_error_km": (
                    result.comparison.mean_error_km
                ),
                "maximum_error_km": (
                    result.comparison.maximum_error_km
                ),
                "rms_error_km": (
                    result.comparison.rms_error_km
                ),
                "radial_rms_km": (
                    radial_rms
                ),
                "along_track_rms_km": (
                    along_track_rms
                ),
                "cross_track_rms_km": (
                    cross_track_rms
                ),
            }

            for (
                threshold,
                reliability,
            ) in result.reliability.items():

                label = (
                    f"{threshold:g}"
                    .replace(".", "_")
                )

                if (
                    reliability.crossed_threshold
                ):
                    reported_horizon = (
                        reliability.horizon_hours
                    )

                    status = "exact"

                else:
                    reported_horizon = (
                        reliability.maximum_observed_hours
                    )

                    status = "lower_bound"

                row[
                    f"horizon_"
                    f"{label}km_hours"
                ] = reported_horizon

                row[
                    f"horizon_"
                    f"{label}km_status"
                ] = status

                row[
                    f"crossed_"
                    f"{label}km"
                ] = (
                    reliability.crossed_threshold
                )

            writer.writerow(
                row
            )


# ---------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run ORBIT-TRUST batch reliability "
            "analysis for a supported satellite."
        )
    )

    parser.add_argument(
        "--satellite",
        choices=sorted(
            SATELLITES.keys()
        ),
        default=DEFAULT_SATELLITE_KEY,
        help=(
            "Satellite to analyze. "
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
            "If omitted, the newest .tle file "
            "for the selected satellite is used."
        ),
    )

    return parser.parse_args()


# ---------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------

def run_batch_analysis(
    satellite_key: str,
    tle_file: Path | None = None,
) -> Path:
    """
    Run the complete 24-hour reliability experiment for one satellite.
    """

    ensure_app_directories()

    satellite = get_satellite(
        satellite_key
    )

    tle_catalog_file = (
        find_tle_catalog(
            satellite_key=satellite.key,
            requested_file=tle_file,
        )
    )

    eof_folder = get_reference_dir(
        satellite.key
    )

    output_file = (
        get_batch_result_file(
            satellite.key
        )
    )

    print("=" * 70)
    print(
        "ORBIT-TRUST Batch Reliability Analysis"
    )
    print("=" * 70)

    print(
        f"Satellite:        "
        f"{satellite.name}"
    )

    print(
        f"Mission code:     "
        f"{satellite.short_name}"
    )

    print(
        f"NORAD catalog:    "
        f"{satellite.norad_id}"
    )

    print(
        f"TLE catalog:      "
        f"{tle_catalog_file}"
    )

    print(
        f"Reference folder: "
        f"{eof_folder}"
    )

    print(
        f"Output CSV:       "
        f"{output_file}"
    )

    print()

    tle_entries = load_tle_catalog(
        tle_catalog_file
    )

    reference_states = (
        load_all_reference_states(
            eof_folder
        )
    )

    results: list[
        BatchResult
    ] = []

    print(
        f"TLEs loaded: "
        f"{len(tle_entries)}"
    )

    print(
        f"Reference states loaded: "
        f"{len(reference_states)}"
    )

    print()

    for number, tle in enumerate(
        tle_entries,
        start=1,
    ):
        print(
            f"[{number}/"
            f"{len(tle_entries)}] "
            f"Analyzing "
            f"{satellite.name} "
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
            print()

            continue

        results.append(
            result
        )

        errors = (
            result.comparison.errors
        )

        observation_hours = (
            errors[-1].time
            - errors[0].time
        ).total_seconds() / 3600.0

        print(
            f"  Matched records: "
            f"{len(errors)}"
        )

        print(
            f"  Observation window: "
            f"{observation_hours:.2f} h"
        )

        print(
            f"  Initial error: "
            f"{errors[0].total_error_km:.3f} km"
        )

        print(
            f"  Final error: "
            f"{errors[-1].total_error_km:.3f} km"
        )

        print(
            f"  Mean error: "
            f"{result.comparison.mean_error_km:.3f} km"
        )

        print(
            f"  RMS error: "
            f"{result.comparison.rms_error_km:.3f} km"
        )

        for (
            threshold,
            reliability,
        ) in result.reliability.items():

            if (
                reliability.crossed_threshold
                and (
                    reliability.horizon_hours
                    is not None
                )
            ):
                status = (
                    f"{reliability.horizon_hours:.2f} h"
                )

            else:
                status = (
                    ">"
                    f"{reliability.maximum_observed_hours:.2f} h"
                )

            print(
                f"  {threshold:g} km horizon: "
                f"{status}"
            )

        print()

    if not results:
        raise RuntimeError(
            f"No {satellite.name} TLEs could "
            "be analyzed with a complete "
            "24-hour precise-orbit window."
        )

    export_batch_results(
        results=results,
        output_path=output_file,
    )

    print("=" * 70)

    print(
        f"Completed analyses: "
        f"{len(results)}"
    )

    print(
        f"Skipped analyses: "
        f"{len(tle_entries) - len(results)}"
    )

    print(
        f"Results saved to:\n"
        f"{output_file}"
    )

    print("=" * 70)

    return output_file


def main() -> None:
    args = parse_arguments()

    run_batch_analysis(
        satellite_key=args.satellite,
        tle_file=args.tle_file,
    )


if __name__ == "__main__":
    main()