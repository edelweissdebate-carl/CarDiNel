from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from src.core.batch_analyzer import run_batch_analysis
from src.core.statistics_engine import generate_statistics
from src.core.report_generator import generate_report
from src.core.time_error_statistics import run_time_error_statistics
from src.core.timeseries_exporter import export_timeseries
from src.core.tle_sampler import sample_catalog
from src.core.satellites import get_satellite
from src.io.download_precise_orbits import download_precise_orbits
from src.io.download_tles import download_tles


SCOPE_REPORT_LABELS = {
    "sentinel1a": "Sentinel-1A",
    "sentinel1b": "Sentinel-1B",
    "combined": "Combined",
}


@dataclass
class SatellitePipelineResult:
    satellite_key: str
    raw_tle_file: Path
    sampled_tle_file: Path
    batch_result_file: Path
    timeseries_file: Path
    statistics_folder: Path
    time_error_statistics_folder: Path


def run_satellite_pipeline(
    satellite_key: str,
    start_date: date,
    end_date: date,
    alpha: float = 0.05,
    force_tle_download: bool = False,
) -> SatellitePipelineResult:
    """Run the complete ORBIT-TRUST scientific workflow for one satellite."""

    if end_date < start_date:
        raise ValueError("End date cannot be earlier than start date.")

    satellite = get_satellite(satellite_key)

    # Space-Track range queries use the supplied date as a boundary.
    # Extend retrieval by one calendar day so the entire final study day
    # is available to the sampler. The sampler still restricts the actual
    # experimental sample to start_date through end_date.
    tle_download_end_date = end_date + timedelta(days=1)

    print()
    print("#" * 78)
    print(f"STARTING ORBIT-TRUST PIPELINE: {satellite.name}")
    print("#" * 78)
    print()

    print()
    print("=" * 78)
    print(f"STEP 1 — Historical TLE acquisition: {satellite.name}")
    print("=" * 78)
    print(
        f"Study sampling dates: {start_date.isoformat()} through "
        f"{end_date.isoformat()} (inclusive)"
    )
    print(
        f"Space-Track retrieval boundary: {start_date.isoformat()} through "
        f"{tle_download_end_date.isoformat()}"
    )

    raw_tle_file = download_tles(
        satellite_key=satellite.key,
        start_date=start_date,
        end_date=tle_download_end_date,
        force_download=force_tle_download,
    )

    print()
    print("=" * 78)
    print(f"STEP 2 — Daily TLE sampling: {satellite.name}")
    print("=" * 78)

    sampled_tle_file = sample_catalog(
        satellite_key=satellite.key,
        start_date=start_date,
        end_date=end_date,
        source_file=raw_tle_file,
    )

    reference_end_date = end_date + timedelta(days=1)

    print()
    print("=" * 78)
    print(f"STEP 3 — Precise-orbit acquisition: {satellite.name}")
    print("=" * 78)
    print(
        "Reference coverage intentionally extends one day beyond the sampling period."
    )
    print(
        f"Reference coverage: {start_date.isoformat()} through "
        f"{reference_end_date.isoformat()}"
    )

    download_precise_orbits(
        satellite_key=satellite.key,
        start_date=start_date,
        end_date=reference_end_date,
    )

    print()
    print("=" * 78)
    print(f"STEP 4 — Batch reliability analysis: {satellite.name}")
    print("=" * 78)

    batch_result_file = run_batch_analysis(
        satellite_key=satellite.key,
        tle_file=sampled_tle_file,
    )

    print()
    print("=" * 78)
    print(f"STEP 5 — Detailed error time-series: {satellite.name}")
    print("=" * 78)

    timeseries_file = export_timeseries(
        satellite_key=satellite.key,
        tle_file=sampled_tle_file,
    )

    print()
    print("=" * 78)
    print(f"STEP 6 — Batch statistics: {satellite.name}")
    print("=" * 78)

    statistics_folder = generate_statistics(scope=satellite.key)

    print()
    print("=" * 78)
    print(f"STEP 7 — Propagation time/error statistics: {satellite.name}")
    print("=" * 78)

    time_error_folder = run_time_error_statistics(
        scope=satellite.key,
        alpha=alpha,
    )

    print()
    print("#" * 78)
    print(f"COMPLETED ORBIT-TRUST PIPELINE: {satellite.name}")
    print("#" * 78)
    print(f"Raw TLE catalog:\n{raw_tle_file}")
    print(f"Sampled TLE catalog:\n{sampled_tle_file}")
    print(f"Batch results:\n{batch_result_file}")
    print(f"Time-series results:\n{timeseries_file}")
    print(f"Statistics:\n{statistics_folder}")
    print(f"Time/error statistics:\n{time_error_folder}")

    return SatellitePipelineResult(
        satellite_key=satellite.key,
        raw_tle_file=raw_tle_file,
        sampled_tle_file=sampled_tle_file,
        batch_result_file=batch_result_file,
        timeseries_file=timeseries_file,
        statistics_folder=statistics_folder,
        time_error_statistics_folder=time_error_folder,
    )


def run_research_pipeline(
    scope: str,
    start_date: date,
    end_date: date,
    alpha: float = 0.05,
    force_tle_download: bool = False,
) -> list[SatellitePipelineResult]:
    """Run the ORBIT-TRUST research experiment."""

    if scope == "both":
        satellite_keys = ["sentinel1a", "sentinel1b"]
    elif scope in {"sentinel1a", "sentinel1b"}:
        satellite_keys = [scope]
    else:
        raise ValueError(f"Unsupported research scope: {scope}")

    results: list[SatellitePipelineResult] = []

    print()
    print("=" * 78)
    print("ORBIT-TRUST MASTER RESEARCH PIPELINE")
    print("=" * 78)
    print(f"Research scope: {scope}")
    print(
        f"Sampling period: {start_date.isoformat()} through "
        f"{end_date.isoformat()} (inclusive)"
    )
    print("Sampling rule: earliest valid TLE epoch per UTC day")
    print("Propagation window: 24 hours per sampled TLE")
    print("Error thresholds: 1 km, 2 km, 5 km, 10 km")
    print(f"Statistical alpha: {alpha}")
    print("=" * 78)

    for satellite_key in satellite_keys:
        result = run_satellite_pipeline(
            satellite_key=satellite_key,
            start_date=start_date,
            end_date=end_date,
            alpha=alpha,
            force_tle_download=force_tle_download,
        )
        results.append(result)

    if len(satellite_keys) == 2:
        print()
        print("=" * 78)
        print("STEP 8 — Combined Sentinel-1A/Sentinel-1B statistics")
        print("=" * 78)

        combined_statistics_folder = generate_statistics(scope="combined")

        print()
        print("=" * 78)
        print("STEP 9 — Combined propagation time/error statistics")
        print("=" * 78)

        combined_time_error_folder = run_time_error_statistics(
            scope="combined",
            alpha=alpha,
        )

        print()
        print("Combined batch statistics:")
        print(combined_statistics_folder)
        print()
        print("Combined time/error statistics:")
        print(combined_time_error_folder)

    # -----------------------------------------------------------------
    # Final PDF research report
    # -----------------------------------------------------------------

    report_scope = (
        "combined"
        if scope == "both"
        else scope
    )

    report_step = (
        10
        if scope == "both"
        else 8
    )

    print()
    print("=" * 78)
    print(
        f"STEP {report_step} — Generate PDF research report"
    )
    print("=" * 78)

    report_file = generate_report(
        scope=report_scope
    )

    print()
    print(
        f"Research report:\n{report_file}"
    )

    print()
    print("#" * 78)
    print("ORBIT-TRUST RESEARCH PIPELINE FINISHED SUCCESSFULLY")
    print("#" * 78)
    print(f"Satellites completed: {len(results)}")

    for result in results:
        satellite = get_satellite(result.satellite_key)
        print(f"  ✓ {satellite.name}")

    if len(results) == 2:
        print("  ✓ Combined statistical analysis")

    print(
        f"  ✓ {SCOPE_REPORT_LABELS[report_scope]} PDF report"
    )

    print()
    print(
        f"Final report:\n{report_file}"
    )
    print("#" * 78)

    return results


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the complete ORBIT-TRUST multi-satellite research pipeline."
        )
    )

    parser.add_argument(
        "--satellite",
        choices=["sentinel1a", "sentinel1b", "both"],
        default="both",
        help="Satellite research scope. Default: both",
    )

    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        required=True,
        help="First inclusive sampling date in YYYY-MM-DD format.",
    )

    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        required=True,
        help="Last inclusive sampling date in YYYY-MM-DD format.",
    )

    parser.add_argument(
        "--alpha",
        type=float,
        default=0.05,
        help="Statistical significance level. Default: 0.05",
    )

    parser.add_argument(
        "--force-tles",
        action="store_true",
        help=(
            "Force historical TLEs to be downloaded again instead of using "
            "a cached raw catalog."
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if args.end < args.start:
        raise ValueError("End date cannot be earlier than start date.")

    if not (0.0 < args.alpha < 1.0):
        raise ValueError("Alpha must be between 0 and 1.")

    run_research_pipeline(
        scope=args.satellite,
        start_date=args.start,
        end_date=args.end,
        alpha=args.alpha,
        force_tle_download=args.force_tles,
    )


if __name__ == "__main__":
    main()