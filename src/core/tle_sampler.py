from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path

from src.core.paths import (
    get_tle_dir,
)
from src.core.satellites import (
    DEFAULT_SATELLITE_KEY,
    SATELLITES,
    get_satellite,
)
from src.io.tle_reader import (
    TLEData,
    load_tle_catalog,
)


# ---------------------------------------------------------------------
# Catalog discovery
# ---------------------------------------------------------------------

def find_source_catalog(
    satellite_key: str,
    requested_file: Path | None = None,
) -> Path:
    """
    Locate the historical TLE catalog that will be sampled.
    """

    satellite = get_satellite(
        satellite_key
    )

    if requested_file is not None:
        requested_file = (
            requested_file.resolve()
        )

        if not requested_file.exists():
            raise FileNotFoundError(
                "Requested TLE catalog was not found:\n"
                f"{requested_file}"
            )

        return requested_file

    tle_directory = get_tle_dir(
        satellite.key
    )

    candidates = [
        path
        for path in tle_directory.glob("*.tle")
        if "_sampled_" not in path.name
    ]

    candidates.sort(
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not candidates:
        raise FileNotFoundError(
            f"No unsampled historical TLE catalog was found "
            f"for {satellite.name} in:\n"
            f"{tle_directory}"
        )

    return candidates[0]


# ---------------------------------------------------------------------
# Daily sampling
# ---------------------------------------------------------------------

def select_earliest_tle_per_day(
    entries: list[TLEData],
    start_date: date,
    end_date: date,
) -> list[TLEData]:
    """
    Select the earliest valid TLE epoch within each UTC calendar day.

    TLEData epochs are expected to be timezone-aware UTC datetimes.
    """

    if end_date < start_date:
        raise ValueError(
            "End date cannot be earlier than start date."
        )

    grouped: dict[
        date,
        list[TLEData],
    ] = defaultdict(list)

    for tle in entries:
        tle_date = tle.epoch.date()

        if (
            start_date
            <= tle_date
            <= end_date
        ):
            grouped[tle_date].append(
                tle
            )

    selected: list[TLEData] = []

    current_date = start_date

    while current_date <= end_date:

        candidates = grouped.get(
            current_date,
            [],
        )

        if candidates:
            candidates.sort(
                key=lambda tle: tle.epoch
            )

            selected.append(
                candidates[0]
            )

        current_date = (
            date.fromordinal(
                current_date.toordinal() + 1
            )
        )

    return selected


# ---------------------------------------------------------------------
# Catalog export
# ---------------------------------------------------------------------

def build_output_file(
    satellite_key: str,
    start_date: date,
    end_date: date,
) -> Path:
    satellite = get_satellite(
        satellite_key
    )

    return (
        get_tle_dir(
            satellite.key
        )
        / (
            f"{satellite.key}_sampled_"
            f"{start_date.isoformat()}_to_"
            f"{end_date.isoformat()}.tle"
        )
    )


def write_sampled_catalog(
    entries: list[TLEData],
    output_file: Path,
) -> None:
    """
    Write selected TLEs in three-line format.
    """

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    lines: list[str] = []

    for number, tle in enumerate(
        entries,
        start=1,
    ):
        lines.extend(
            [
                f"Sampled TLE {number}",
                tle.line_1,
                tle.line_2,
            ]
        )

    output_file.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------
# Main sampling workflow
# ---------------------------------------------------------------------

def sample_catalog(
    satellite_key: str,
    start_date: date,
    end_date: date,
    source_file: Path | None = None,
) -> Path:
    satellite = get_satellite(
        satellite_key
    )

    input_file = find_source_catalog(
        satellite_key=satellite.key,
        requested_file=source_file,
    )

    print("=" * 70)
    print(
        "ORBIT-TRUST Daily TLE Sampling"
    )
    print("=" * 70)

    print(
        f"Satellite:       {satellite.name}"
    )

    print(
        f"Mission code:    {satellite.short_name}"
    )

    print(
        f"Sampling period: "
        f"{start_date.isoformat()} "
        f"through {end_date.isoformat()}"
    )

    print(
        "Sampling rule:   "
        "earliest valid TLE epoch per UTC day"
    )

    print(
        f"Source catalog:  {input_file}"
    )

    print()

    entries = load_tle_catalog(
        input_file
    )

    selected = (
        select_earliest_tle_per_day(
            entries=entries,
            start_date=start_date,
            end_date=end_date,
        )
    )

    if not selected:
        raise RuntimeError(
            "No TLEs satisfied the sampling period."
        )

    output_file = build_output_file(
        satellite_key=satellite.key,
        start_date=start_date,
        end_date=end_date,
    )

    write_sampled_catalog(
        entries=selected,
        output_file=output_file,
    )

    expected_days = (
        end_date.toordinal()
        - start_date.toordinal()
        + 1
    )

    print(
        f"Valid catalog entries: "
        f"{len(entries)}"
    )

    print(
        f"Expected calendar days: "
        f"{expected_days}"
    )

    print(
        f"Selected TLEs: "
        f"{len(selected)}"
    )

    print()

    for tle in selected:
        print(
            f"  {tle.epoch.date().isoformat()} "
            f"→ {tle.epoch.isoformat()}"
        )

    missing_days = (
        expected_days
        - len(selected)
    )

    print()

    if missing_days:
        print(
            f"WARNING: {missing_days} calendar day(s) "
            "had no valid TLE available."
        )
    else:
        print(
            "All requested calendar days were represented."
        )

    print()

    print(
        f"Sampled catalog saved to:\n"
        f"{output_file}"
    )

    print("=" * 70)

    return output_file


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Select one historical TLE per UTC day "
            "for the ORBIT-TRUST experiment."
        )
    )

    parser.add_argument(
        "--satellite",
        choices=sorted(
            SATELLITES.keys()
        ),
        default=DEFAULT_SATELLITE_KEY,
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
        "--source-file",
        type=Path,
        default=None,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    sample_catalog(
        satellite_key=args.satellite,
        start_date=args.start,
        end_date=args.end,
        source_file=args.source_file,
    )


if __name__ == "__main__":
    main()