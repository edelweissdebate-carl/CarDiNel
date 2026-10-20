"""Evaluate historical Sentinel TLEs on an exact epoch-based time grid.

These are retrospective reconstructions, never prospective captures.
Shares reference checks, transformations, interpolation and horizon reporting
with prospective_validation. Does not alter legacy outputs or captured forecasts.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import shutil
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from sgp4.api import Satrec
from sgp4.io import verify_checksum

from src.core.paths import RESULTS_DIR, get_reference_dir
from src.core.propagator import tle_epoch_datetime
from src.core.prospective_capture import write_predictions
from src.core.prospective_validation import (
    FIELDS, PRODUCTS, ReferenceUnavailable, State, digest, horizon_summary,
    interpolate_position, read_references, transform_predictions, utc,
)
from src.core.satellites import SATELLITES, get_satellite
from src.io.tle_reader import TLEData, load_tle_catalog


def checked_satrec(tle: TLEData, norad_id: int) -> Satrec:
    lines = (tle.line_1.strip(), tle.line_2.strip())
    if (any(len(line) != 69 for line in lines)
            or not lines[0].startswith("1 ") or not lines[1].startswith("2 ")
            or any(int(line[2:7]) != norad_id for line in lines)):
        raise ValueError("Historical TLE lines do not match the selected satellite.")
    verify_checksum(*lines)
    satellite = Satrec.twoline2rv(*lines)
    if tle.epoch.tzinfo is None or tle.epoch.utcoffset() is None:
        raise ValueError("Historical TLE epoch must be timezone-aware.")
    if abs((tle_epoch_datetime(satellite) - tle.epoch).total_seconds()) > 0.00001:
        raise ValueError("Historical TLE epoch does not match its orbital elements.")
    return satellite


def evaluate_tle(
    satellite_key: str, tle: TLEData, reference_files: list[Path], output_root: Path,
    *, product: str = "POE", duration_hours: float = 24, step_seconds: int = 60,
    thresholds: tuple[float, ...] = (1, 2, 5, 10),
    max_gap_seconds: float = 20, make_plot: bool = True,
    source_catalog: Path | None = None, expected_catalog_hash: str | None = None,
) -> Path:
    """Publish one complete historical evaluation, or no result on failure."""
    started = perf_counter()
    config = get_satellite(satellite_key)
    if (not math.isfinite(duration_hours) or duration_hours <= 0
            or not math.isfinite(duration_hours * 3600)):
        raise ValueError("Duration must be finite and positive.")
    if isinstance(step_seconds, bool) or not isinstance(step_seconds, int) or step_seconds <= 0:
        raise ValueError("Step must be a positive integer number of seconds.")
    if not thresholds or len(set(thresholds)) != len(thresholds):
        raise ValueError("Provide distinct positive thresholds.")
    for threshold in thresholds:
        horizon_summary([tle.epoch], [0.0], threshold)
    satellite = checked_satrec(tle, config.norad_id)
    origin = tle.epoch.astimezone(timezone.utc)
    end = origin + timedelta(hours=duration_hours)
    reference_files = [Path(path) for path in reference_files]
    references, provenance = read_references(
        reference_files, config.name, product, origin, end,
        max_gap_seconds=max_gap_seconds,
    )
    source_catalog = Path(source_catalog) if source_catalog is not None else None
    if source_catalog is not None:
        catalog_hash = digest(source_catalog)
        if expected_catalog_hash is not None and catalog_hash != expected_catalog_hash:
            raise ValueError("Input catalog changed after it was loaded.")
    else:
        catalog_hash = None
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    case_name = f"{config.key}_{origin.strftime('%Y%m%dT%H%M%S%fZ')}_{product}_{uuid4().hex[:12]}"
    destination = output_root / case_name
    with tempfile.TemporaryDirectory(dir=output_root, prefix=".partial-") as temporary:
        staging = Path(temporary)
        tle_path = staging / "evaluated_tle.tle"
        tle_path.write_text(tle.line_1.strip() + "\n" + tle.line_2.strip() + "\n", encoding="utf-8")
        if source_catalog is not None:
            archived_catalog = staging / "input_catalog.tle"
            shutil.copyfile(source_catalog, archived_catalog)
            if digest(archived_catalog) != catalog_hash:
                raise ValueError("Input catalog changed while being archived.")
        prediction_file = staging / "predictions_teme.csv"
        prediction_count = write_predictions(satellite, origin, duration_hours, step_seconds, prediction_file)
        with prediction_file.open(encoding="utf-8", newline="") as stream:
            predictions = []
            for row in csv.DictReader(stream):
                values = tuple(float(row[field]) for field in FIELDS)
                predictions.append(State(utc(row["time_utc"]), values[:3], values[3:]))
        if predictions[0].time != origin or predictions[-1].time != end or len(predictions) != prediction_count:
            raise ValueError("Historical reconstruction does not include both requested endpoints.")
        positions, transformation = transform_predictions(predictions, staging)
        if len(positions) != len(predictions):
            raise ValueError("Frame transformation returned an incomplete trajectory.")
        reference_times = [state.time for state in references]
        times = [state.time for state in predictions]
        errors = []
        with (staging / "errors.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("time_utc", "elapsed_hours_from_tle_epoch",
                             "pred_x_itrs_km", "pred_y_itrs_km", "pred_z_itrs_km",
                             "ref_x_itrs_km", "ref_y_itrs_km", "ref_z_itrs_km", "total_error_km"))
            for state, position in zip(predictions, positions):
                reference = interpolate_position(references, state.time, reference_times)
                error = math.dist(position, reference)
                if not math.isfinite(error):
                    raise ValueError("Historical evaluation produced a non-finite error.")
                errors.append(error)
                writer.writerow((state.time.isoformat(),
                                 (state.time - origin).total_seconds() / 3600,
                                 *position, *reference, error))
        horizons = [horizon_summary(times, errors, threshold) for threshold in thresholds]
        with (staging / "horizons.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(horizons[0]))
            writer.writeheader()
            writer.writerows(horizons)
        archive = staging / "references"
        archive.mkdir()
        for index, item in enumerate(provenance):
            target = archive / f"{index:03d}_{item['filename']}"
            shutil.copyfile(item["source_path"], target)
            if digest(target) != item["sha256"]:
                raise ValueError("Reference changed during historical evaluation.")
            item["archived_file"] = str(target.relative_to(staging))
        if make_plot:
            import matplotlib
            matplotlib.use("Agg")
            from matplotlib import pyplot as plt
            figure, axis = plt.subplots(figsize=(9, 5))
            ages = [(stamp - origin).total_seconds() / 3600 for stamp in times]
            axis.plot(ages, errors, label="3D position error")
            for threshold in thresholds:
                axis.axhline(threshold, linestyle="--", alpha=0.5, label=f"{threshold:g} km")
            axis.set(xlabel="Hours since TLE epoch", ylabel="Position error (km)",
                     title=f"{config.name}: historical reconstruction vs {product}")
            axis.legend()
            figure.tight_layout()
            figure.savefig(staging / "error_plot.png", dpi=180)
            plt.close(figure)
        if source_catalog is not None and digest(source_catalog) != catalog_hash:
            raise ValueError("Input catalog changed during historical evaluation.")
        summary = {
            "schema_version": 1, "evaluation_mode": "retrospective",
            "status": f"historically_evaluated_against_{product.lower()}",
            "satellite_key": config.key, "satellite_name": config.name,
            "norad_id": config.norad_id, "tle_epoch_utc": origin.isoformat(),
            "evaluation_origin_utc": origin.isoformat(), "evaluation_end_utc": end.isoformat(),
            "duration_hours": duration_hours, "step_seconds": step_seconds,
            "sample_count": len(errors), "reference_grade": product,
            "reference_product": PRODUCTS[product],
            "source_catalog_sha256": catalog_hash, "evaluated_tle_sha256": digest(tle_path),
            "case_group": f"{config.norad_id}:{digest(tle_path)}",
            "mean_error_km": sum(errors) / len(errors), "maximum_error_km": max(errors),
            "rms_error_km": math.sqrt(sum(value * value for value in errors) / len(errors)),
            "horizons": horizons, "references": provenance,
            "frame_transformation": transformation,
            "reference_interpolation": "Cubic Hermite; Earth-fixed positions and velocities; no extrapolation",
            "maximum_reference_gap_seconds": max_gap_seconds,
            "overlap_rule": "Latest Creation_Date wins at each overlapping reference timestamp",
            "horizon_definition": "First sampled exceedance measured from TLE epoch; bracket and linear estimate separate",
            "limitations": [
                "Historical reconstruction does not establish prospective forecasting.",
                "TLE epoch is not a record of its public availability or acquisition time.",
                "Crossings between samples can be missed; linear estimates are not exact crossing times.",
                "Right-censored horizons remain unknown beyond the evaluated duration.",
                "Cases sharing an input or overlapping windows are not independent trials.",
                "EARTH_FIXED is treated as geocentric ITRS; terrestrial realization differences are not modelled.",
                "Synthetic software checks do not establish real-reference scientific accuracy.",
            ],
            "python_version": platform.python_version(),
            "package_versions": {name: version(name) for name in ("astropy", "numpy", "sgp4", "astropy-iers-data")},
            "historical_module_sha256": digest(Path(__file__)),
            "shared_validator_module_sha256": digest(Path(__file__).with_name("prospective_validation.py")),
            "capture_module_sha256": digest(Path(__file__).with_name("prospective_capture.py")),
            "propagator_module_sha256": digest(Path(__file__).with_name("propagator.py")),
            "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
            "processing_seconds_before_summary": perf_counter() - started,
        }
        summary["output_sha256"] = {path.name: digest(path) for path in staging.iterdir() if path.is_file()}
        (staging / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        staging.rename(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--satellite", choices=sorted(SATELLITES), required=True)
    parser.add_argument("--tle-file", type=Path, required=True,
                        help="Explicit input catalog, e.g. output from tle_sampler.")
    parser.add_argument("--reference-dir", type=Path)
    parser.add_argument("--product", choices=list(PRODUCTS), default="POE")
    parser.add_argument("--output-root", type=Path, default=RESULTS_DIR / "historical_validation")
    parser.add_argument("--duration-hours", type=float, default=24)
    parser.add_argument("--step-seconds", type=int, default=60)
    parser.add_argument("--thresholds-km", type=float, nargs="+", default=[1, 2, 5, 10])
    parser.add_argument("--max-reference-gap-seconds", type=float, default=20)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    try:
        catalog_hash = digest(args.tle_file)
        tles = load_tle_catalog(args.tle_file)
        if digest(args.tle_file) != catalog_hash:
            raise ValueError("Input catalog changed while being loaded.")
        config = get_satellite(args.satellite)
        reference_dir = args.reference_dir if args.reference_dir is not None else get_reference_dir(config.key)
        references = sorted(path for path in reference_dir.rglob("*")
                            if path.is_file() and path.suffix.upper() == ".EOF"
                            and path.name.startswith(config.short_name + "_")
                            and f"_{PRODUCTS[args.product]}_" in path.name)
        if not tles:
            raise ValueError("No TLE entries found.")
        # Check the whole catalog before publishing any cases.
        for tle in tles:
            checked_satrec(tle, config.norad_id)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"Historical evaluation failed: {error}\n")
    failed = 0
    for index, tle in enumerate(tles, 1):
        try:
            output = evaluate_tle(
                config.key, tle, references, args.output_root, product=args.product,
                duration_hours=args.duration_hours, step_seconds=args.step_seconds,
                thresholds=tuple(args.thresholds_km), max_gap_seconds=args.max_reference_gap_seconds,
                make_plot=not args.no_plot, source_catalog=args.tle_file,
                expected_catalog_hash=catalog_hash,
            )
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            print(f"[{index}/{len(tles)}] HISTORICAL {config.name}: {tle.epoch.isoformat()}")
            print(f"  Samples: {summary['sample_count']}; duration: {summary['duration_hours']:g} h")
            print(f"  RMS error: {summary['rms_error_km']:.6f} km")
            for horizon in summary["horizons"]:
                threshold = horizon["threshold_km"]
                if horizon["right_censored"]:
                    print(f"  {threshold:g} km: not reached in {horizon['observed_duration_hours']:g} h (right-censored)")
                else:
                    print(f"  {threshold:g} km: first sampled exceedance {horizon['first_exceedance_hours']:.6f} h; "
                          f"linear estimate {horizon['horizon_hours_estimate']:.6f} h")
            print(f"  Results: {output}")
        except (ValueError, KeyError, OSError, RuntimeError, ET.ParseError) as error:
            failed += 1
            print(f"[{index}/{len(tles)}] FAILED {tle.epoch.isoformat()}: {error}")
    if failed:
        parser.exit(1, f"{failed} historical case(s) could not be evaluated.\n")


if __name__ == "__main__":
    main()
