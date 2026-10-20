"""Synthetic offline checks; these are not measurements of satellite accuracy."""
import csv
import hashlib
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
from astropy.utils import iers

from src.core.prospective_capture import capture_forecast
from src.core.satellites import SatelliteConfig, get_satellite
from src.core.prospective_validation import (
    ReferenceUnavailable, State, horizon_summary, interpolate_position,
    load_capture, read_references, transform_predictions, validate_capture,
)

BASE = datetime(2019, 12, 10, tzinfo=timezone.utc)
TLE = (
    "1 25544U 98067A   19343.69339541  .00001764  00000-0  38792-4 0  9991\n"
    "2 25544  51.6439 211.2001 0007417  17.6667  85.6398 15.50103472202482\n"
)
CONFIG = SatelliteConfig("fixture", "Sentinel-1A", "S1A", 25544)


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference_xml(states, mission="Sentinel-1A", product="AUX_POEORB",
                  frame="EARTH_FIXED", time_reference="UTC", unit="m", creation=None):
    vectors = []
    for state in states:
        fields = "".join(
            f'<{field} unit="{unit if index < 3 else "m/s"}">{value * 1000:.12f}</{field}>'
            for index, (field, value) in enumerate(zip(("X", "Y", "Z", "VX", "VY", "VZ"),
                                                      (*state.position, *state.velocity)))
        )
        vectors.append(f"<OSV><UTC>UTC={state.time.isoformat()}</UTC>{fields}</OSV>")
    return (
        "<Earth_Explorer_File><Earth_Explorer_Header><Fixed_Header>"
        f"<Mission>{mission}</Mission><File_Type>{product}</File_Type>"
        f"<Validity_Period><Validity_Start>UTC={states[0].time.isoformat()}</Validity_Start>"
        f"<Validity_Stop>UTC={states[-1].time.isoformat()}</Validity_Stop></Validity_Period>"
        f"<Source><Creation_Date>UTC={(creation or BASE).isoformat()}</Creation_Date></Source>"
        "</Fixed_Header><Variable_Header>"
        f"<Ref_Frame>{frame}</Ref_Frame><Time_Reference>{time_reference}</Time_Reference>"
        "</Variable_Header></Earth_Explorer_Header><Data_Block>"
        f'<List_of_OSVs count="{len(states)}">{"".join(vectors)}</List_of_OSVs>'
        "</Data_Block></Earth_Explorer_File>"
    )


class HorizonTests(unittest.TestCase):
    def times(self):
        return [BASE + timedelta(hours=index) for index in range(4)]

    def test_first_crossing_and_interpolation_bracket(self):
        result = horizon_summary(self.times(), [0, 0.5, 2, 0], 1)
        self.assertAlmostEqual(result["horizon_hours_estimate"], 4 / 3)
        self.assertEqual(result["first_exceedance_hours"], 2)
        self.assertEqual((result["bracket_start_hours"], result["bracket_end_hours"]), (1, 2))
        self.assertFalse(result["right_censored"])

    def test_initial_exceedance_is_zero(self):
        result = horizon_summary(self.times(), [1, 0, 2, 3], 1)
        self.assertEqual(result["horizon_hours_estimate"], 0)
        self.assertTrue(result["crossed_threshold"])

    def test_no_crossing_is_censored_not_equal_to_window(self):
        result = horizon_summary(self.times(), [0, 0.2, 0.3, 0.4], 1)
        self.assertTrue(result["right_censored"])
        self.assertIsNone(result["horizon_hours_estimate"])
        self.assertEqual(result["observed_duration_hours"], 3)

    def test_invalid_errors_and_thresholds(self):
        for values, threshold in (([0, float("nan"), 1, 2], 1),
                                  ([0, -1, 1, 2], 1), ([0, 1, 2, 3], 0),
                                  ([0, 1, 2, 3], float("inf"))):
            with self.subTest(values=values, threshold=threshold), self.assertRaises(ValueError):
                horizon_summary(self.times(), values, threshold)
        with self.assertRaises(ValueError):
            horizon_summary(list(reversed(self.times())), [0, 1, 2, 3], 1)


class InterpolationTests(unittest.TestCase):
    def test_cubic_trajectory_at_fractional_seconds(self):
        def state(seconds):
            return State(BASE + timedelta(seconds=seconds),
                         (seconds**3, 2 * seconds**2, 7.0),
                         (3 * seconds**2, 4 * seconds, 0.0))
        result = interpolate_position([state(0), state(10)], BASE + timedelta(seconds=4.564816))
        np.testing.assert_allclose(result, state(4.564816).position, rtol=0, atol=1e-10)

    def test_smooth_orbit_interpolation_error_below_one_centimetre(self):
        # Independent analytic circular trajectory, representative LEO angular rate.
        omega, radius = 2 * math.pi / 6000, 7000
        def state(seconds):
            angle = seconds * omega
            return State(BASE + timedelta(seconds=seconds),
                         (radius * math.cos(angle), radius * math.sin(angle), 0.0),
                         (-radius * omega * math.sin(angle), radius * omega * math.cos(angle), 0.0))
        states = [state(0), state(10)]
        for seconds in (0.000001, 1.1, 4.564816, 5.0, 9.9, 10):
            with self.subTest(seconds=seconds):
                estimate = interpolate_position(states, state(seconds).time)
                self.assertLess(math.dist(estimate, state(seconds).position), 0.00001)

    def test_no_extrapolation(self):
        state = State(BASE, (1, 2, 3), (0, 0, 0))
        with self.assertRaises(ReferenceUnavailable):
            interpolate_position([state], BASE + timedelta(seconds=1))


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for target in ("src.core.prospective_capture.get_satellite",
                       "src.core.prospective_validation.get_satellite"):
            mock = patch(target, return_value=CONFIG)
            mock.start()
            self.addCleanup(mock.stop)
        self.capture = capture_forecast(
            "fixture", lambda identifier: TLE, duration_hours=1 / 60, step_seconds=30,
            output_root=self.root / "captures", clock=lambda: BASE,
        )
        self.manifest, self.predictions = load_capture(self.capture)
        self.origin = self.predictions[0].time
        self.end = self.predictions[-1].time
        self.output = self.root / "results"
        self.reference = self.root / "S1A_OPER_AUX_POEORB_fixture.EOF"
        self.states = [State(self.origin + timedelta(seconds=index * 10),
                             (7000.0, 0.0, 0.0), (0.0, 0.0, 0.0)) for index in range(7)]
        self.reference.write_text(reference_xml(self.states), encoding="utf-8")

    def test_capture_hash_tampering_is_rejected(self):
        with (self.capture / "predictions_teme.csv").open("a") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            load_capture(self.capture)

    def test_missing_row_even_with_updated_hash_is_rejected(self):
        path = self.capture / "predictions_teme.csv"
        lines = path.read_text().splitlines()
        path.write_text("\n".join(lines[:-1]) + "\n")
        manifest = self.manifest.copy()
        manifest["sha256"]["predictions_teme.csv"] = hash_file(path)
        (self.capture / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "row count"):
            load_capture(self.capture)

    def test_wrong_identity_product_frame_time_units_are_rejected(self):
        for options in ({"mission": "Sentinel-1C"}, {"product": "AUX_MOEORB"},
                        {"frame": "TEME"}, {"time_reference": "TAI"}, {"unit": "km"}):
            with self.subTest(options=options):
                self.reference.write_text(reference_xml(self.states, **options), encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_references([self.reference], CONFIG.name, "POE", self.origin, self.end)

    def test_missing_coverage_and_large_gap_are_pending(self):
        for states in (self.states[1:], self.states[:-1], [self.states[0], self.states[-1]]):
            with self.subTest(count=len(states)):
                self.reference.write_text(reference_xml(states), encoding="utf-8")
                with self.assertRaises(ReferenceUnavailable):
                    read_references([self.reference], CONFIG.name, "POE", self.origin, self.end)

    def test_latest_created_product_wins_in_overlap(self):
        newer = self.root / "newer.EOF"
        states = [State(state.time, (7100.0, 0.0, 0.0), state.velocity) for state in self.states]
        newer.write_text(reference_xml(states, creation=BASE + timedelta(days=1)), encoding="utf-8")
        result, provenance = read_references([newer, self.reference], CONFIG.name, "POE", self.origin, self.end)
        self.assertEqual(result[0].position, (7100, 0, 0))
        self.assertEqual(len(provenance), 2)

    def test_output_and_capture_preservation_with_known_error_series(self):
        before = {path.name: path.read_bytes() for path in self.capture.iterdir()}
        positions = [(7000.0 + offset, 0.0, 0.0) for offset in (0.0, 1.5, 3.0)]
        with patch("src.core.prospective_validation.transform_predictions", return_value=(positions, {"fixture": True})):
            result = validate_capture(self.capture, [self.reference], self.output,
                                      thresholds=(1, 5), make_plot=True)
        summary = json.loads((result / "summary.json").read_text())
        self.assertEqual(summary["status"], "validated_against_poe")
        self.assertEqual(summary["reference_grade"], "POE")
        self.assertAlmostEqual(summary["horizons"][0]["horizon_hours_estimate"], 20 / 3600)
        self.assertTrue(summary["horizons"][1]["right_censored"])
        self.assertEqual(summary["maximum_error_km"], 3)
        with (result / "errors.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([float(row["total_error_km"]) for row in rows], [0, 1.5, 3])
        self.assertEqual({path.name: path.read_bytes() for path in self.capture.iterdir()}, before)
        self.assertTrue((result / "error_plot.png").exists())
        reference_copy = result / summary["references"][0]["archived_file"]
        self.assertEqual(hash_file(reference_copy), hash_file(self.reference))

    def test_moe_is_separate_and_explicitly_labelled(self):
        self.reference.write_text(reference_xml(self.states, product="AUX_MOEORB"), encoding="utf-8")
        with patch("src.core.prospective_validation.transform_predictions",
                   return_value=([(7000, 0, 0)] * 3, {"fixture": True})):
            result = validate_capture(self.capture, [self.reference], self.output,
                                      product="MOE", make_plot=False)
        summary = json.loads((result / "summary.json").read_text())
        self.assertEqual(summary["status"], "validated_against_moe")
        self.assertEqual(summary["reference_product"], "AUX_MOEORB")

    def test_failed_validation_publishes_no_results(self):
        with patch("src.core.prospective_validation.transform_predictions", side_effect=ValueError("fixture failure")):
            with self.assertRaisesRegex(ValueError, "fixture failure"):
                validate_capture(self.capture, [self.reference], self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_output_must_be_outside_capture(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            validate_capture(self.capture, [self.reference], self.capture / "analysis")


class FrameTests(unittest.TestCase):
    def test_real_astropy_transform_preserves_radius_and_archives_iers(self):
        states = [State(BASE, (7000.0, 10.0, 20.0), (0.0, 7.0, 0.0)),
                  State(BASE + timedelta(seconds=60), (6900, 400, 100), (0, 7, 0))]
        with tempfile.TemporaryDirectory() as folder, iers.conf.set_temp("auto_download", False):
            result, metadata = transform_predictions(states, Path(folder))
            for state, position in zip(states, result):
                self.assertAlmostEqual(math.dist(state.position, (0, 0, 0)),
                                       math.dist(position, (0, 0, 0)), places=8)
            self.assertGreater(math.dist(states[0].position, result[0]), 100)
            self.assertEqual(metadata["earth_orientation_sha256"],
                             hash_file(Path(folder) / "earth_orientation.ecsv"))

    def test_out_of_range_earth_orientation_is_rejected(self):
        state = State(datetime(2100, 1, 1, tzinfo=timezone.utc), (7000, 0, 0), (0, 7, 0))
        with tempfile.TemporaryDirectory() as folder, iers.conf.set_temp("auto_download", False):
            with self.assertRaisesRegex(ValueError, "Earth orientation"):
                transform_predictions([state], Path(folder))


class RegistryTests(unittest.TestCase):
    def test_sentinel1c_catalog_identity(self):
        self.assertEqual(get_satellite("sentinel1c").norad_id, 62261)
        self.assertTrue(get_satellite("sentinel1c").prospective_collection_enabled)


if __name__ == "__main__":
    unittest.main()
