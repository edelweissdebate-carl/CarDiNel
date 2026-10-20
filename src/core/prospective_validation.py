"""Validate archived prospective forecasts against Sentinel EOF reference files.

No forecasts or capture manifests are rewritten. Full reference coverage is
required. POE and MOE validations are separate, explicitly labelled results.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import platform
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from sgp4.api import Satrec
from sgp4.io import verify_checksum

from src.core.propagator import tle_epoch_datetime
from src.core.satellites import get_satellite
from src.io.eof_parser import element_text, parse_utc


FIELDS = ("x_km", "y_km", "z_km", "vx_km_s", "vy_km_s", "vz_km_s")
PRODUCTS = {"POE": "AUX_POEORB", "MOE": "AUX_MOEORB"}


class ReferenceUnavailable(ValueError):
    """The reference does not yet cover the complete forecast."""


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            result.update(block)
    return result.hexdigest()


def utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Forecast timestamps must specify a timezone.")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class State:
    time: datetime
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]


def load_capture(folder: Path) -> tuple[dict, list[State]]:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported capture schema.")
    config = get_satellite(manifest["satellite_key"])
    if manifest["norad_id"] != config.norad_id:
        raise ValueError("Manifest satellite identity does not match the registry.")
    if (manifest["prediction_frame"], manifest["position_unit"], manifest["velocity_unit"]) != (
        "TEME", "km", "km/s"
    ):
        raise ValueError("Expected forecast states in TEME, km and km/s.")
    for filename in ("source_response.tle", "predictions_teme.csv"):
        if digest(folder / filename) != manifest["sha256"].get(filename):
            raise ValueError(f"Capture hash mismatch: {filename}")
    lines = (folder / "source_response.tle").read_text(encoding="utf-8").splitlines()
    lines = [line.strip() for line in lines if line.strip()]
    if (len(lines) != 2 or any(len(line) != 69 for line in lines)
            or not lines[0].startswith("1 ") or not lines[1].startswith("2 ")
            or any(int(line[2:7]) != config.norad_id for line in lines)):
        raise ValueError("Saved TLE identity or format is invalid.")
    verify_checksum(*lines)
    epoch = tle_epoch_datetime(Satrec.twoline2rv(*lines))
    if abs((epoch - utc(manifest["tle_epoch_utc"])).total_seconds()) > 0.00001:
        raise ValueError("Manifest TLE epoch does not match the saved TLE.")
    order = [utc(manifest[name]) for name in (
        "acquisition_started_utc", "acquired_at_utc", "generation_started_utc",
        "capture_completed_utc", "forecast_origin_utc", "forecast_end_utc"
    )]
    if order != sorted(order) or order[3] >= order[4] or order[4] >= order[5]:
        raise ValueError("Capture does not establish a forecast completed before its origin.")
    step = manifest["step_seconds"]
    duration = manifest["duration_hours"] * 3600.0
    if (isinstance(step, bool) or not isinstance(step, int) or step <= 0
            or not math.isfinite(duration) or duration <= 0):
        raise ValueError("Invalid forecast grid settings.")
    if abs((order[5] - order[4]).total_seconds() - duration) > 0.000001:
        raise ValueError("Forecast endpoints disagree with its duration.")
    expected_count = math.ceil(duration / step) + 1
    states = []
    with (folder / "predictions_teme.csv").open(encoding="utf-8", newline="") as stream:
        for index, row in enumerate(csv.DictReader(stream)):
            timestamp = utc(row["time_utc"])
            values = tuple(float(row[field]) for field in FIELDS)
            if not all(math.isfinite(value) for value in values):
                raise ValueError("Forecast contains non-finite coordinates.")
            expected_time = order[4] + timedelta(seconds=min(index * step, duration))
            if index >= expected_count or timestamp != expected_time:
                raise ValueError("Forecast timestamps do not match the archived grid.")
            states.append(State(timestamp, values[:3], values[3:]))
    if len(states) != expected_count or len(states) != manifest["prediction_rows"]:
        raise ValueError("Forecast row count does not match the manifest.")
    return manifest, states


def reference_header(path: Path, mission: str, product: str) -> tuple[ET.Element, datetime, datetime]:
    root = ET.parse(path).getroot()
    if element_text(root, "Mission").upper() != mission.upper():
        raise ValueError(f"Wrong reference satellite in {path.name}.")
    if element_text(root, "File_Type") != PRODUCTS[product]:
        raise ValueError(f"Wrong reference product in {path.name}; expected {product}.")
    if element_text(root, "Ref_Frame").replace("-", "_").upper() != "EARTH_FIXED":
        raise ValueError(f"Unsupported reference frame in {path.name}.")
    if element_text(root, "Time_Reference").upper() != "UTC":
        raise ValueError(f"Unsupported reference time system in {path.name}.")
    start = parse_utc(element_text(root, "Validity_Start"))
    stop = parse_utc(element_text(root, "Validity_Stop"))
    if stop < start:
        raise ValueError(f"Invalid reference validity period in {path.name}.")
    return root, start, stop


def read_references(
    paths: list[Path], mission: str, product: str, origin: datetime, end: datetime,
    *, max_gap_seconds: float = 20.0,
) -> tuple[list[State], list[dict]]:
    """Use newer-created products at overlapping epochs, deterministically."""
    if product not in PRODUCTS:
        raise ValueError("Reference product must be POE or MOE.")
    if not math.isfinite(max_gap_seconds) or max_gap_seconds <= 0:
        raise ValueError("Maximum reference gap must be positive and finite.")
    records = {}
    provenance = []
    candidates = []
    for path in paths:
        root, start, stop = reference_header(path, mission, product)
        if stop < origin or start > end:
            continue
        creation = parse_utc(element_text(root, "Creation_Date"))
        candidates.append((creation, path.name, str(path.resolve()), path, root, start, stop))
    for creation, _, _, path, root, start, stop in sorted(candidates):
        entries = [entry for entry in root.iter() if entry.tag.split("}")[-1] == "OSV"]
        lists = [entry for entry in root.iter() if entry.tag.split("}")[-1] == "List_of_OSVs"]
        if len(lists) != 1 or int(lists[0].attrib["count"]) != len(entries):
            raise ValueError(f"Reference OSV count mismatch in {path.name}.")
        previous = None
        for entry in entries:
            timestamp = parse_utc(element_text(entry, "UTC"))
            if previous is not None and timestamp <= previous:
                raise ValueError(f"Reference timestamps are not strictly increasing in {path.name}.")
            previous = timestamp
            if not start <= timestamp <= stop:
                raise ValueError(f"OSV outside its declared validity in {path.name}.")
            values = []
            for field, unit in zip(("X", "Y", "Z", "VX", "VY", "VZ"),
                                   ("m", "m", "m", "m/s", "m/s", "m/s")):
                matches = [node for node in entry if node.tag.split("}")[-1] == field]
                if len(matches) != 1 or matches[0].attrib.get("unit") != unit:
                    raise ValueError(f"Missing or unsupported {field} unit in {path.name}.")
                values.append(float(matches[0].text) / 1000.0)
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"Non-finite reference coordinates in {path.name}.")
            records[timestamp] = State(timestamp, tuple(values[:3]), tuple(values[3:]))
        provenance.append({
            "filename": path.name, "source_path": str(path.resolve()), "sha256": digest(path),
            "creation_utc": creation.isoformat(), "validity_start_utc": start.isoformat(),
            "validity_stop_utc": stop.isoformat(),
        })
    states = sorted(records.values(), key=lambda state: state.time)
    if not states or states[0].time > origin or states[-1].time < end:
        raise ReferenceUnavailable("Reference does not cover the complete forecast; validation is pending.")
    for left, right in zip(states, states[1:]):
        if right.time < origin or left.time > end:
            continue
        if (right.time - left.time).total_seconds() > max_gap_seconds:
            raise ReferenceUnavailable("Reference has a gap across the forecast; validation is pending.")
    return states, provenance


def interpolate_position(states: list[State], timestamp: datetime, times=None) -> tuple[float, float, float]:
    """Cubic Hermite interpolation using Earth-fixed position and velocity.

    Requires bracketing samples; never extrapolates or rounds the forecast time.
    """
    if times is None:
        times = [state.time for state in states]
    index = bisect.bisect_left(times, timestamp)
    if index < len(states) and states[index].time == timestamp:
        return states[index].position
    if index == 0 or index == len(states):
        raise ReferenceUnavailable("A forecast timestamp is not bracketed by reference states.")
    left, right = states[index - 1], states[index]
    interval = (right.time - left.time).total_seconds()
    fraction = (timestamp - left.time).total_seconds() / interval
    a = 2 * fraction**3 - 3 * fraction**2 + 1
    b = fraction**3 - 2 * fraction**2 + fraction
    c = -2 * fraction**3 + 3 * fraction**2
    d = fraction**3 - fraction**2
    return tuple(a * left.position[i] + b * interval * left.velocity[i]
                 + c * right.position[i] + d * interval * right.velocity[i] for i in range(3))


def horizon_summary(times: list[datetime], errors: list[float], threshold: float) -> dict:
    """First sampled exceedance, with an explicitly estimated crossing bracket."""
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("Thresholds must be finite and positive.")
    if (len(times) != len(errors) or not times
            or any(not math.isfinite(value) or value < 0 for value in errors)
            or any(right <= left for left, right in zip(times, times[1:]))):
        raise ValueError("Invalid error series.")
    observed = (times[-1] - times[0]).total_seconds() / 3600
    result = dict(threshold_km=threshold, crossed_threshold=False, right_censored=True,
                  horizon_hours_estimate=None, first_exceedance_hours=None,
                  bracket_start_hours=None, bracket_end_hours=None,
                  observed_duration_hours=observed)
    for index, value in enumerate(errors):
        if value < threshold:
            continue
        upper = (times[index] - times[0]).total_seconds() / 3600
        lower = 0.0 if index == 0 else (times[index - 1] - times[0]).total_seconds() / 3600
        fraction = 0.0 if index == 0 else (threshold - errors[index - 1]) / (value - errors[index - 1])
        result.update(crossed_threshold=True, right_censored=False,
                      horizon_hours_estimate=lower + fraction * (upper - lower),
                      first_exceedance_hours=upper, bracket_start_hours=lower, bracket_end_hours=upper)
        break
    return result


def transform_predictions(states: list[State], output: Path) -> tuple[list[tuple], dict]:
    """Transform archived TEME states and retain the IERS table used."""
    import numpy as np
    import astropy.units as u
    from astropy.coordinates import CartesianRepresentation, ITRS, TEME
    from astropy.time import Time
    from astropy.utils import iers

    times = Time([state.time for state in states])
    table = iers.IERS_Auto.open()
    # Astropy may return boundary values for out-of-range times: reject them.
    _, ut1_status = table.ut1_utc(times, return_status=True)
    _, _, polar_status = table.pm_xy(times, return_status=True)
    if np.any(np.asarray(ut1_status) < 0) or np.any(np.asarray(polar_status) < 0):
        raise ValueError("Earth orientation data do not cover the forecast. Update astropy-iers-data or allow its download.")
    with iers.earth_orientation_table.set(table):
        data = np.asarray([state.position for state in states]).T
        frame = TEME(CartesianRepresentation(data * u.km), obstime=times)
        fixed = frame.transform_to(ITRS(obstime=times))
    positions = fixed.cartesian.xyz.to_value(u.km).T
    if not np.isfinite(positions).all():
        raise ValueError("Frame transformation returned non-finite coordinates.")
    table.write(output / "earth_orientation.ecsv", format="ascii.ecsv")
    return [tuple(row) for row in positions], {
        "method": "Astropy TEME to geocentric ITRS at saved forecast timestamps",
        "earth_orientation_file": "earth_orientation.ecsv",
        "earth_orientation_sha256": digest(output / "earth_orientation.ecsv"),
        "uses_predicted_earth_orientation": bool(np.any(np.asarray(ut1_status) == iers.FROM_IERS_A_PREDICTION)
                                                or np.any(np.asarray(polar_status) == iers.FROM_IERS_A_PREDICTION)),
    }


def validate_capture(
    capture_folder: Path, reference_files: list[Path], output_root: Path,
    *, product: str = "AUTO", thresholds: tuple[float, ...] = (1, 2, 5, 10),
    max_gap_seconds: float = 20.0, make_plot: bool = True,
) -> Path:
    capture_folder, output_root = Path(capture_folder), Path(output_root)
    if output_root.resolve() == capture_folder.resolve() or capture_folder.resolve() in output_root.resolve().parents:
        raise ValueError("Validation outputs must be outside the archived capture.")
    if not thresholds or len(set(thresholds)) != len(thresholds):
        raise ValueError("Provide distinct positive thresholds.")
    for threshold in thresholds:
        horizon_summary([datetime(2000, 1, 1, tzinfo=timezone.utc)], [0.0], threshold)
    manifest, predictions = load_capture(capture_folder)
    config = get_satellite(manifest["satellite_key"])
    from src.io.orbit_references import resolve_product, load_reference_states, native_product, overlap_description
    product = resolve_product(config, product)
    references, provenance = load_reference_states(
        reference_files, config, product, predictions[0].time, predictions[-1].time,
        max_gap_seconds=max_gap_seconds,
    )
    manifest_hash = digest(capture_folder / "manifest.json")
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / f"{capture_folder.name}_{product}_{uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(dir=output_root, prefix=".partial-") as temporary:
        staging = Path(temporary)
        positions, transform = transform_predictions(predictions, staging)
        reference_times = [state.time for state in references]
        errors = []
        with (staging / "errors.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("time_utc", "forecast_age_hours", "tle_age_hours",
                             "pred_x_itrs_km", "pred_y_itrs_km", "pred_z_itrs_km",
                             "ref_x_itrs_km", "ref_y_itrs_km", "ref_z_itrs_km", "total_error_km"))
            for state, position in zip(predictions, positions):
                reference = interpolate_position(references, state.time, reference_times)
                error = math.dist(position, reference)
                errors.append(error)
                writer.writerow((state.time.isoformat(),
                                 (state.time - predictions[0].time).total_seconds() / 3600,
                                 (state.time - utc(manifest["tle_epoch_utc"])).total_seconds() / 3600,
                                 *position, *reference, error))
        horizons = [horizon_summary([state.time for state in predictions], errors, threshold)
                    for threshold in thresholds]
        with (staging / "horizons.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(horizons[0]))
            writer.writeheader()
            writer.writerows(horizons)
        reference_dir = staging / "references"
        reference_dir.mkdir()
        for index, item in enumerate(provenance):
            target = reference_dir / f"{index:03d}_{item['filename']}"
            shutil.copyfile(item["source_path"], target)
            if digest(target) != item["sha256"]:
                raise ValueError("Reference changed during validation.")
            item["archived_file"] = str(target.relative_to(staging))
        summary = {
            "schema_version": 1, "status": f"validated_against_{product.lower()}",
            "capture_id": manifest["capture_id"], "satellite_key": config.key,
            "satellite_name": config.name, "norad_id": config.norad_id,
            "reference_product": native_product(config, product), "reference_grade": product,
            "capture_manifest_sha256": manifest_hash,
            "source_tle_sha256": manifest["sha256"]["source_response.tle"],
            "case_group": f"{config.norad_id}:{manifest['sha256']['source_response.tle']}",
            "forecast_origin_utc": predictions[0].time.isoformat(),
            "forecast_end_utc": predictions[-1].time.isoformat(),
            "tle_epoch_utc": manifest["tle_epoch_utc"],
            "tle_age_at_origin_hours": (predictions[0].time - utc(manifest["tle_epoch_utc"])).total_seconds() / 3600,
            "sample_count": len(errors), "maximum_error_km": max(errors),
            "rms_error_km": math.sqrt(sum(value * value for value in errors) / len(errors)),
            "horizons": horizons, "references": provenance, "frame_transformation": transform,
            "reference_interpolation": "Cubic Hermite, Earth-fixed positions and velocities; no extrapolation",
            "maximum_reference_gap_seconds": max_gap_seconds,
            "overlap_rule": overlap_description(config),
            "horizon_definition": "First sampled exceedance; linear crossing estimate and bracket also reported",
            "limitations": [
                "Crossings between forecast samples can be missed.",
                "An interpolated crossing is an estimate, not an exact crossing time.",
                "A censored horizon is unknown beyond the observation window.",
                "Captures sharing a TLE or overlapping forecast windows are not independent cases.",
                "Hashes and local timestamps do not constitute external timestamp certification.",
                "Earth-fixed reference coordinates are treated as geocentric ITRS; terrestrial realization differences are not modelled.",
            ],
            "python_version": platform.python_version(),
            "package_versions": {name: version(name) for name in ("astropy", "numpy", "sgp4", "astropy-iers-data")},
            "validator_module_sha256": digest(Path(__file__)),
            "reference_adapter_sha256": digest(Path(__file__).parents[1] / "io" / "orbit_references.py"),
            "swarm_reader_sha256": digest(Path(__file__).parents[1] / "io" / "swarm_orbits.py") if config.reference_provider == "swarm_sp3" else None,
            "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        if make_plot:
            import matplotlib
            matplotlib.use("Agg")
            from matplotlib import pyplot as plt
            figure, axis = plt.subplots(figsize=(9, 5))
            ages = [(state.time - predictions[0].time).total_seconds() / 3600 for state in predictions]
            axis.plot(ages, errors, label="3D position error", linewidth=1.3)
            for threshold in thresholds:
                axis.axhline(threshold, linestyle="--", alpha=0.5, label=f"{threshold:g} km")
            axis.set(xlabel="Hours since forecast origin", ylabel="Position error (km)",
                     title=f"{config.name}: prospective forecast vs {product}")
            axis.legend()
            figure.tight_layout()
            figure.savefig(staging / "error_plot.png", dpi=180)
            plt.close(figure)
        # Reject changes made while the analysis was running; never edit the capture.
        if digest(capture_folder / "manifest.json") != manifest_hash:
            raise ValueError("Capture manifest changed during validation.")
        for name, expected in manifest["sha256"].items():
            if name in ("source_response.tle", "predictions_teme.csv") and digest(capture_folder / name) != expected:
                raise ValueError("Capture changed during validation.")
        summary["output_sha256"] = {path.name: digest(path) for path in staging.iterdir() if path.is_file()}
        (staging / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        staging.rename(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--capture", type=Path)
    targets.add_argument("--captures-root", type=Path, help="Recursively find capture manifests.")
    parser.add_argument("--reference-dir", type=Path, required=True, help="Directory containing Sentinel EOF or official Swarm ZIP reference files.")
    parser.add_argument("--product", choices=["AUTO", "POE", "MOE", "PSO"], default="AUTO")
    parser.add_argument("--output-root", type=Path, default=Path("results/prospective_validation"))
    parser.add_argument("--thresholds-km", type=float, nargs="+", default=[1, 2, 5, 10])
    parser.add_argument("--max-reference-gap-seconds", type=float, default=20)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    captures = [args.capture] if args.capture else sorted(
        path.parent for path in args.captures_root.rglob("manifest.json")
        if not any(part.startswith(".partial-") for part in path.parts)
    )
    if not captures:
        parser.exit(1, "No captures found.\n")
    from src.io.orbit_references import reference_files as select_reference_files, resolve_product
    failed = 0
    for capture in captures:
        try:
            # Header validation still checks identity; filename filtering permits
            # a shared reference directory containing several satellites/products.
            manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
            config = get_satellite(manifest["satellite_key"])
            product = resolve_product(config, args.product)
            selected = select_reference_files(args.reference_dir, config, product)
            output = validate_capture(capture, selected, args.output_root, product=product,
                                      thresholds=tuple(args.thresholds_km),
                                      max_gap_seconds=args.max_reference_gap_seconds, make_plot=not args.no_plot)
            print(f"VALIDATED ({product}): {capture.name}\nResults: {output}")
        except ReferenceUnavailable as error:
            failed += 1
            print(f"PENDING: {capture.name}: {error}")
        except (ValueError, KeyError, OSError, RuntimeError, ET.ParseError) as error:
            failed += 1
            print(f"FAILED: {capture.name}: {error}")
    if failed:
        parser.exit(1, f"{failed} capture(s) remain unvalidated. No capture files were changed.\n")


if __name__ == "__main__":
    main()
