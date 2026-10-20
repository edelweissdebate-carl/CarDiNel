from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from pathlib import Path


@dataclass
class OrbitRecord:
    time: datetime
    x_km: float
    y_km: float
    z_km: float
    vx_km_s: float
    vy_km_s: float
    vz_km_s: float


@dataclass
class ErrorRecord:
    time: datetime
    total_error_km: float
    radial_error_km: float
    along_track_error_km: float
    cross_track_error_km: float


@dataclass
class ComparisonResult:
    errors: list[ErrorRecord]
    mean_error_km: float
    maximum_error_km: float
    rms_error_km: float


def load_orbit_csv(file_path: str | Path) -> list[OrbitRecord]:
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Orbit file not found:\n{path}")

    records: list[OrbitRecord] = []

    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)

        required_columns = {
            "time_utc",
            "x_km",
            "y_km",
            "z_km",
            "vx_km_s",
            "vy_km_s",
            "vz_km_s",
        }

        fieldnames = set(reader.fieldnames or [])

        missing_columns = required_columns - fieldnames

        if missing_columns:
            missing_text = ", ".join(sorted(missing_columns))
            raise ValueError(
                f"The CSV is missing required columns: {missing_text}"
            )

        for row_number, row in enumerate(reader, start=2):
            try:
                records.append(
                    OrbitRecord(
                        time=datetime.fromisoformat(
                            row["time_utc"].replace("Z", "+00:00")
                        ),
                        x_km=float(row["x_km"]),
                        y_km=float(row["y_km"]),
                        z_km=float(row["z_km"]),
                        vx_km_s=float(row["vx_km_s"]),
                        vy_km_s=float(row["vy_km_s"]),
                        vz_km_s=float(row["vz_km_s"]),
                    )
                )

            except Exception as error:
                raise ValueError(
                    f"Invalid data on CSV row {row_number}: {error}"
                ) from error

    if not records:
        raise ValueError("The selected CSV contains no orbit records.")

    return records


def vector_dot(
    vector_a: tuple[float, float, float],
    vector_b: tuple[float, float, float],
) -> float:
    return sum(a * b for a, b in zip(vector_a, vector_b))


def vector_cross(
    vector_a: tuple[float, float, float],
    vector_b: tuple[float, float, float],
) -> tuple[float, float, float]:
    ax, ay, az = vector_a
    bx, by, bz = vector_b

    return (
        ay * bz - az * by,
        az * bx - ax * bz,
        ax * by - ay * bx,
    )


def vector_norm(vector: tuple[float, float, float]) -> float:
    return sqrt(vector_dot(vector, vector))


def normalize(
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    magnitude = vector_norm(vector)

    if magnitude == 0:
        raise ValueError("Cannot normalize a zero-length vector.")

    return tuple(component / magnitude for component in vector)


def compare_orbits(
    predicted_records: list[OrbitRecord],
    reference_records: list[OrbitRecord],
    time_tolerance_seconds: float = 1.0,
) -> ComparisonResult:
    reference_by_time = {
        round(record.time.timestamp()): record
        for record in reference_records
    }

    errors: list[ErrorRecord] = []

    for predicted in predicted_records:
        predicted_timestamp = predicted.time.timestamp()
        rounded_timestamp = round(predicted_timestamp)

        reference = reference_by_time.get(rounded_timestamp)

        if reference is None:
            continue

        time_difference = abs(
            predicted_timestamp - reference.time.timestamp()
        )

        if time_difference > time_tolerance_seconds:
            continue

        error_vector = (
            predicted.x_km - reference.x_km,
            predicted.y_km - reference.y_km,
            predicted.z_km - reference.z_km,
        )

        position_vector = (
            reference.x_km,
            reference.y_km,
            reference.z_km,
        )

        velocity_vector = (
            reference.vx_km_s,
            reference.vy_km_s,
            reference.vz_km_s,
        )

        radial_unit = normalize(position_vector)

        orbit_normal = normalize(
            vector_cross(position_vector, velocity_vector)
        )

        along_track_unit = normalize(
            vector_cross(orbit_normal, radial_unit)
        )

        radial_error = vector_dot(error_vector, radial_unit)
        along_track_error = vector_dot(
            error_vector,
            along_track_unit,
        )
        cross_track_error = vector_dot(
            error_vector,
            orbit_normal,
        )

        total_error = vector_norm(error_vector)

        errors.append(
            ErrorRecord(
                time=predicted.time,
                total_error_km=total_error,
                radial_error_km=radial_error,
                along_track_error_km=along_track_error,
                cross_track_error_km=cross_track_error,
            )
        )

    if not errors:
        raise ValueError(
            "No matching timestamps were found between the two orbit files."
        )

    total_errors = [
        error.total_error_km
        for error in errors
    ]

    mean_error = sum(total_errors) / len(total_errors)
    maximum_error = max(total_errors)

    rms_error = sqrt(
        sum(error**2 for error in total_errors)
        / len(total_errors)
    )

    return ComparisonResult(
        errors=errors,
        mean_error_km=mean_error,
        maximum_error_km=maximum_error,
        rms_error_km=rms_error,
    )