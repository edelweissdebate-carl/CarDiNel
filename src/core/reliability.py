from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.core.comparator import ErrorRecord


@dataclass
class ReliabilityResult:
    threshold_km: float
    horizon_hours: float | None
    crossed_threshold: bool
    crossing_time: datetime | None
    maximum_observed_hours: float
    initial_error_km: float
    final_error_km: float


def calculate_reliability_horizon(
    errors: list[ErrorRecord],
    threshold_km: float,
) -> ReliabilityResult:
    """
    Find when the total position error first crosses a selected threshold.

    Linear interpolation is used between the last point below the threshold
    and the first point above it.
    """
    if not errors:
        raise ValueError("No error records were provided.")

    if threshold_km <= 0:
        raise ValueError("The error threshold must be greater than zero.")

    sorted_errors = sorted(errors, key=lambda record: record.time)
    start_time = sorted_errors[0].time

    maximum_observed_hours = (
        sorted_errors[-1].time - start_time
    ).total_seconds() / 3600.0

    first_error = sorted_errors[0]

    # The prediction is already beyond the limit at the starting time.
    if first_error.total_error_km >= threshold_km:
        return ReliabilityResult(
            threshold_km=threshold_km,
            horizon_hours=0.0,
            crossed_threshold=True,
            crossing_time=first_error.time,
            maximum_observed_hours=maximum_observed_hours,
            initial_error_km=first_error.total_error_km,
            final_error_km=sorted_errors[-1].total_error_km,
        )

    for previous, current in zip(
        sorted_errors,
        sorted_errors[1:],
    ):
        previous_error = previous.total_error_km
        current_error = current.total_error_km

        if (
            previous_error < threshold_km
            and current_error >= threshold_km
        ):
            error_change = current_error - previous_error

            if error_change == 0:
                fraction = 0.0
            else:
                fraction = (
                    threshold_km - previous_error
                ) / error_change

            interval_seconds = (
                current.time - previous.time
            ).total_seconds()

            crossing_seconds_after_previous = (
                fraction * interval_seconds
            )

            horizon_seconds = (
                previous.time - start_time
            ).total_seconds() + crossing_seconds_after_previous

            crossing_time = previous.time + (
                current.time - previous.time
            ) * fraction

            return ReliabilityResult(
                threshold_km=threshold_km,
                horizon_hours=horizon_seconds / 3600.0,
                crossed_threshold=True,
                crossing_time=crossing_time,
                maximum_observed_hours=maximum_observed_hours,
                initial_error_km=first_error.total_error_km,
                final_error_km=sorted_errors[-1].total_error_km,
            )

    # The error stayed below the selected threshold for the whole dataset.
    return ReliabilityResult(
        threshold_km=threshold_km,
        horizon_hours=None,
        crossed_threshold=False,
        crossing_time=None,
        maximum_observed_hours=maximum_observed_hours,
        initial_error_km=first_error.total_error_km,
        final_error_km=sorted_errors[-1].total_error_km,
    )