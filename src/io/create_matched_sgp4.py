from __future__ import annotations

import csv
from pathlib import Path

from sgp4.api import Satrec, jday

from src.io.eof_parser import parse_eof_file
from src.io.frame_converter import teme_to_itrs
from src.io.tle_reader import load_tle_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]

TLE_FILE = (
    PROJECT_ROOT
    / "data"
    / "tle"
    / "sentinel1a_2024-01-01.tle"
)

EOF_FOLDER = (
    PROJECT_ROOT
    / "data"
    / "reference"
    / "sentinel1a"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "results"
    / "sentinel1a_sgp4_itrs.csv"
)


def datetime_to_julian(time):
    return jday(
        time.year,
        time.month,
        time.day,
        time.hour,
        time.minute,
        time.second + time.microsecond / 1_000_000,
    )


def main() -> None:
    tle = load_tle_file(TLE_FILE)

    eof_files = list(EOF_FOLDER.glob("*.EOF"))

    if not eof_files:
        raise FileNotFoundError(
            f"No EOF file found in:\n{EOF_FOLDER}"
        )

    reference_states = parse_eof_file(eof_files[0])

    satellite = Satrec.twoline2rv(
        tle.line_1,
        tle.line_2,
    )

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    matched_count = 0

    with OUTPUT_FILE.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as csv_file:
        writer = csv.writer(csv_file)

        writer.writerow(
            [
                "time_utc",
                "x_km",
                "y_km",
                "z_km",
                "vx_km_s",
                "vy_km_s",
                "vz_km_s",
            ]
        )

        for reference in reference_states:
            jd, fraction = datetime_to_julian(
                reference.time
            )

            error_code, position, velocity = satellite.sgp4(
                jd,
                fraction,
            )

            if error_code != 0:
                continue

            earth_fixed = teme_to_itrs(
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

            writer.writerow(
                [
                    earth_fixed.time.isoformat(),
                    earth_fixed.x_km,
                    earth_fixed.y_km,
                    earth_fixed.z_km,
                    earth_fixed.vx_km_s,
                    earth_fixed.vy_km_s,
                    earth_fixed.vz_km_s,
                ]
            )

            matched_count += 1

    print(f"Satellite: {tle.name}")
    print(f"Reference states read: {len(reference_states)}")
    print(f"SGP4 states exported: {matched_count}")
    print(f"Output:\n{OUTPUT_FILE}")


if __name__ == "__main__":
    main()