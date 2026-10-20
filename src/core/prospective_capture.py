"""Archive new TLE inputs and SGP4 forecasts before their evaluation times.

This command captures predictions only. POE verification and reliability
horizons must be computed later from the saved predictions and reference data.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import tempfile
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Callable
from uuid import uuid4

from sgp4.api import SGP4_ERRORS, Satrec
from sgp4.io import verify_checksum

from src.core.paths import ENV_FILE, get_satellite_data_dir
from src.core.propagator import datetime_to_julian, tle_epoch_datetime, validate_tle_lines
from src.core.satellites import DEFAULT_SATELLITE_KEY, SATELLITES, get_satellite
from src.io.spacetrack_client import SpaceTrackClient


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Capture timestamps must be timezone-aware.")
    return value.astimezone(timezone.utc)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_predictions(
    satellite: Satrec,
    origin: datetime,
    duration_hours: float,
    step_seconds: int,
    path: Path,
) -> int:
    """Write TEME states, including both the forecast origin and final time."""
    duration_seconds = duration_hours * 3600.0
    intervals = math.ceil(duration_seconds / step_seconds)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("time_utc", "x_km", "y_km", "z_km",
                         "vx_km_s", "vy_km_s", "vz_km_s"))
        for index in range(intervals + 1):
            timestamp = origin + timedelta(
                seconds=min(index * step_seconds, duration_seconds)
            )
            jd, fraction = datetime_to_julian(timestamp)
            error, position, velocity = satellite.sgp4(jd, fraction)
            if error:
                raise RuntimeError(
                    f"SGP4 failed at {timestamp.isoformat()}: "
                    f"{SGP4_ERRORS.get(error, error)}"
                )
            if not all(math.isfinite(value) for value in (*position, *velocity)):
                raise RuntimeError("SGP4 produced non-finite forecast coordinates.")
            writer.writerow((timestamp.isoformat(), *position, *velocity))
    return intervals + 1


def capture_forecast(
    satellite_key: str,
    fetch_tle: Callable[[int], str],
    *,
    duration_hours: float = 24.0,
    step_seconds: int = 60,
    lead_seconds: int = 300,
    output_root: Path | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> Path:
    """Fetch and archive one forecast in a new directory, without overwrites.

    ``fetch_tle`` must acquire the current GP response from Space-Track. Tests
    inject a fixture provider; the CLI always uses the authenticated GP route.
    A completed capture is accepted only if its first forecast time is still
    in the future when all files have been written.
    """
    config = get_satellite(satellite_key)
    if not config.prospective_collection_enabled:
        raise ValueError(
            f"{config.name} is configured for historical evaluation only."
        )
    if not math.isfinite(duration_hours) or duration_hours <= 0:
        raise ValueError("Forecast duration must be finite and greater than zero.")
    if not math.isfinite(duration_hours * 3600):
        raise ValueError("Forecast duration is too large.")
    if isinstance(step_seconds, bool) or not isinstance(step_seconds, int) or step_seconds <= 0:
        raise ValueError("Step size must be a positive integer number of seconds.")
    if isinstance(lead_seconds, bool) or not isinstance(lead_seconds, int) or lead_seconds <= 0:
        raise ValueError("Forecast lead must be a positive integer number of seconds.")

    acquisition_started = as_utc(clock())
    response_text = fetch_tle(config.norad_id)
    acquired_at = as_utc(clock())
    if acquired_at < acquisition_started:
        raise ValueError("The system clock moved backwards during acquisition.")
    lines = [line.strip() for line in response_text.splitlines() if line.strip()]
    if len(lines) != 2:
        raise ValueError("Current collection requires exactly one two-line TLE.")
    validate_tle_lines(*lines)
    if any(len(line) != 69 or int(line[2:7]) != config.norad_id for line in lines):
        raise ValueError("TLE identifiers or line lengths do not match this satellite.")
    verify_checksum(*lines)
    satellite = Satrec.twoline2rv(*lines)
    epoch = tle_epoch_datetime(satellite)

    generation_started = as_utc(clock())
    if generation_started < acquired_at:
        raise ValueError("The system clock moved backwards before generation.")
    origin = generation_started + timedelta(seconds=lead_seconds)
    end = origin + timedelta(hours=duration_hours)
    root = (
        Path(output_root) / config.key if output_root is not None
        else get_satellite_data_dir(config.key) / "prospective"
    )
    root.mkdir(parents=True, exist_ok=True)
    capture_id = f"{acquired_at.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex}"
    destination = root / capture_id

    with tempfile.TemporaryDirectory(dir=root, prefix=".partial-") as temporary:
        staging = Path(temporary)
        response_file = staging / "source_response.tle"
        response_file.write_bytes(response_text.encode("utf-8"))
        forecast_file = staging / "predictions_teme.csv"
        row_count = write_predictions(
            satellite, origin, duration_hours, step_seconds, forecast_file
        )
        files = {
            response_file.name: sha256(response_file),
            forecast_file.name: sha256(forecast_file),
        }
        manifest = {
            "schema_version": 1,
            "capture_id": capture_id,
            "status": "pending_poe_validation",
            "satellite_key": config.key,
            "satellite_name": config.name,
            "norad_id": config.norad_id,
            "source": "Space-Track current GP",
            "source_query": SpaceTrackClient.build_current_query(config.norad_id),
            "acquisition_started_utc": acquisition_started.isoformat(),
            "acquired_at_utc": acquired_at.isoformat(),
            "tle_epoch_utc": epoch.isoformat(),
            "tle_age_at_acquisition_seconds": (acquired_at - epoch).total_seconds(),
            "generation_started_utc": generation_started.isoformat(),
            "forecast_origin_utc": origin.isoformat(),
            "forecast_end_utc": end.isoformat(),
            "duration_hours": duration_hours,
            "step_seconds": step_seconds,
            "lead_seconds": lead_seconds,
            "prediction_rows": row_count,
            "prediction_frame": "TEME",
            "position_unit": "km",
            "velocity_unit": "km/s",
            "propagator": "SGP4",
            "gravity_model": "WGS72 (python-sgp4 default)",
            "python_version": platform.python_version(),
            "sgp4_version": version("sgp4"),
            "capture_module_sha256": sha256(Path(__file__)),
            "propagator_module_sha256": sha256(Path(__file__).with_name("propagator.py")),
            "sha256": files,
        }
        manifest_file = staging / "manifest.json"
        manifest_file.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        completed_at = as_utc(clock())
        if completed_at < generation_started:
            raise ValueError("The system clock moved backwards during generation.")
        if completed_at >= origin:
            raise RuntimeError(
                "Generation reached the first forecast time. Increase --lead-seconds "
                "and capture again; this run was not saved as a prospective forecast."
            )
        manifest["capture_completed_utc"] = completed_at.isoformat()
        manifest_file.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        finalized_at = as_utc(clock())
        if finalized_at < completed_at or finalized_at >= origin:
            raise RuntimeError(
                "The capture could not be finalized before its forecast origin "
                "with a monotonic clock. Increase --lead-seconds and retry."
            )
        staging.rename(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--satellite", default=DEFAULT_SATELLITE_KEY,
        choices=[key for key, value in SATELLITES.items()
                 if value.prospective_collection_enabled],
    )
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--step-seconds", type=int, default=60)
    parser.add_argument("--lead-seconds", type=int, default=300)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=False)
    username = os.getenv("SPACETRACK_USERNAME", "")
    password = os.getenv("SPACETRACK_PASSWORD", "")
    if not username.strip() or not password:
        parser.error(
            "Set SPACETRACK_USERNAME and SPACETRACK_PASSWORD in your local .env file."
        )
    try:
        with SpaceTrackClient(username=username, password=password) as client:
            client.login()
            folder = capture_forecast(
                args.satellite, client.fetch_current_tle,
                duration_hours=args.duration_hours, step_seconds=args.step_seconds,
                lead_seconds=args.lead_seconds, output_root=args.output_root,
            )
    except (ValueError, RuntimeError) as error:
        parser.exit(1, f"Capture failed: {error}\n")
    print(f"Prospective forecast saved in:\n{folder}")
    print("POE comparison and reliability horizons are pending reference availability.")


if __name__ == "__main__":
    main()
