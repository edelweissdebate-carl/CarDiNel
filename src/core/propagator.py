from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import sqrt

from sgp4.api import Satrec, jday


EARTH_RADIUS_KM = 6378.137


@dataclass
class OrbitState:
    time: datetime
    x_km: float
    y_km: float
    z_km: float
    vx_km_s: float
    vy_km_s: float
    vz_km_s: float
    altitude_km: float
    speed_km_s: float


@dataclass
class PropagationResult:
    satellite_name: str
    tle_epoch: datetime
    states: list[OrbitState]


def datetime_to_julian(dt: datetime) -> tuple[float, float]:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return jday(
        dt.year,
        dt.month,
        dt.day,
        dt.hour,
        dt.minute,
        dt.second + dt.microsecond / 1_000_000,
    )


def tle_epoch_datetime(satellite: Satrec) -> datetime:
    short_year = satellite.epochyr

    if short_year < 57:
        full_year = 2000 + short_year
    else:
        full_year = 1900 + short_year

    return datetime(
        full_year,
        1,
        1,
        tzinfo=timezone.utc,
    ) + timedelta(days=satellite.epochdays - 1)


def validate_tle_lines(tle_line_1: str, tle_line_2: str) -> None:
    line_1 = tle_line_1.strip()
    line_2 = tle_line_2.strip()

    if not line_1 or not line_2:
        raise ValueError("Both TLE lines are required.")

    if not line_1.startswith("1 "):
        raise ValueError("TLE Line 1 must begin with '1 '.")

    if not line_2.startswith("2 "):
        raise ValueError("TLE Line 2 must begin with '2 '.")

    if len(line_1) < 60 or len(line_2) < 60:
        raise ValueError(
            "The TLE appears incomplete. Check both lines and try again."
        )


def propagate_tle(
    satellite_name: str,
    tle_line_1: str,
    tle_line_2: str,
    duration_hours: float,
    step_minutes: float,
) -> PropagationResult:
    validate_tle_lines(tle_line_1, tle_line_2)

    if duration_hours <= 0:
        raise ValueError("Duration must be greater than zero.")

    if step_minutes <= 0:
        raise ValueError("Step size must be greater than zero.")

    satellite = Satrec.twoline2rv(
        tle_line_1.strip(),
        tle_line_2.strip(),
    )

    start_time = tle_epoch_datetime(satellite)
    total_minutes = duration_hours * 60
    number_of_steps = int(total_minutes / step_minutes) + 1

    states: list[OrbitState] = []

    for index in range(number_of_steps):
        current_time = start_time + timedelta(
            minutes=index * step_minutes
        )

        jd, fraction = datetime_to_julian(current_time)

        error_code, position, velocity = satellite.sgp4(
            jd,
            fraction,
        )

        if error_code != 0:
            raise RuntimeError(
                f"SGP4 error code {error_code} at "
                f"{current_time.isoformat()}."
            )

        x, y, z = position
        vx, vy, vz = velocity

        distance_from_center = sqrt(x**2 + y**2 + z**2)
        altitude = distance_from_center - EARTH_RADIUS_KM
        speed = sqrt(vx**2 + vy**2 + vz**2)

        states.append(
            OrbitState(
                time=current_time,
                x_km=x,
                y_km=y,
                z_km=z,
                vx_km_s=vx,
                vy_km_s=vy,
                vz_km_s=vz,
                altitude_km=altitude,
                speed_km_s=speed,
            )
        )

    cleaned_name = satellite_name.strip() or "Unnamed Satellite"

    return PropagationResult(
        satellite_name=cleaned_name,
        tle_epoch=start_time,
        states=states,
    )