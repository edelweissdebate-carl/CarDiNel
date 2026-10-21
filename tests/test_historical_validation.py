"""Historical reconstruction checks use synthetic references, not accuracy evidence."""
import csv
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from sgp4.api import Satrec

from src.core.historical_validation import checked_satrec, evaluate_tle
from src.core.propagator import tle_epoch_datetime
from src.core.prospective_capture import write_predictions
from src.core.prospective_validation import ReferenceUnavailable, State, digest
from src.core.satellites import SatelliteConfig
from src.io.tle_reader import TLEData

TLE = (
    "1 25544U 98067A   19343.69339541  .00001764  00000-0  38792-4 0  9991\n"
    "2 25544  51.6439 211.2001 0007417  17.6667  85.6398 15.50103472202482\n"
)
CONFIG = SatelliteConfig("fixture", "Sentinel-1A", "S1A", 25544)


def reference_xml(states, mission="Sentinel-1A", product="AUX_POEORB"):
    vectors = []
    for state in states:
        fields = "".join(
            f'<{field} unit="{unit}">{value * 1000:.12f}</{field}>'
            for field, unit, value in zip(
                ("X", "Y", "Z", "VX", "VY", "VZ"),
                ("m", "m", "m", "m/s", "m/s", "m/s"), (*state.position, *state.velocity))
        )
        vectors.append(f"<OSV><UTC>UTC={state.time.isoformat()}</UTC>{fields}</OSV>")
    return (
        "<Earth_Explorer_File><Earth_Explorer_Header><Fixed_Header>"
        f"<Mission>{mission}</Mission><File_Type>{product}</File_Type>"
        f"<Validity_Period><Validity_Start>UTC={states[0].time.isoformat()}</Validity_Start>"
        f"<Validity_Stop>UTC={states[-1].time.isoformat()}</Validity_Stop></Validity_Period>"
        f"<Source><Creation_Date>UTC={states[0].time.isoformat()}</Creation_Date></Source>"
        "</Fixed_Header><Variable_Header><Ref_Frame>EARTH_FIXED</Ref_Frame>"
        "<Time_Reference>UTC</Time_Reference></Variable_Header></Earth_Explorer_Header>"
        f'<Data_Block><List_of_OSVs count="{len(states)}">{"".join(vectors)}'
        "</List_of_OSVs></Data_Block></Earth_Explorer_File>"
    )


class HistoricalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        lines = TLE.splitlines()
        self.satellite = Satrec.twoline2rv(*lines)
        self.origin = tle_epoch_datetime(self.satellite)
        self.tle = TLEData("fixture", *lines, self.origin)
        self.catalog = self.root / "catalog.tle"
        self.catalog.write_text(TLE, encoding="utf-8")
        self.reference = self.root / "S1A_OPER_AUX_POEORB_fixture.EOF"
        self.references = [
            State(self.origin + timedelta(seconds=seconds), (7000.0, 0.0, 0.0), (0.0, 0.0, 0.0))
            for seconds in range(-5, 76, 10)
        ]
        self.reference.write_text(reference_xml(self.references), encoding="utf-8")
        self.output = self.root / "results"
        mock = patch("src.core.historical_validation.get_satellite", return_value=CONFIG)
        mock.start()
        self.addCleanup(mock.stop)
        self.errors = [0.2, 1.2, 0.4]

    def transform(self, predictions, output):
        (output / "earth_orientation.ecsv").write_text("synthetic table\n", encoding="utf-8")
        return [(7000 + error, 0, 0) for error in self.errors], {"method": "synthetic fixture"}

    def run_case(self, **kwargs):
        defaults = dict(duration_hours=1 / 60, step_seconds=30, make_plot=False,
                        source_catalog=self.catalog, expected_catalog_hash=digest(self.catalog))
        defaults.update(kwargs)
        with patch("src.core.historical_validation.transform_predictions", side_effect=self.transform):
            return evaluate_tle("fixture", self.tle, [self.reference], self.output, **defaults)

    def test_epoch_grid_crossing_bracket_and_separate_estimate(self):
        result = self.run_case()
        summary = json.loads((result / "summary.json").read_text())
        with (result / "errors.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(summary["evaluation_mode"], "retrospective")
        self.assertEqual(summary["sample_count"], 3)
        self.assertEqual(rows[0]["time_utc"], self.origin.isoformat())
        self.assertEqual(float(rows[0]["elapsed_hours_from_tle_epoch"]), 0)
        self.assertAlmostEqual(float(rows[-1]["elapsed_hours_from_tle_epoch"]), 1 / 60)
        crossing = summary["horizons"][0]
        self.assertAlmostEqual(crossing["first_exceedance_hours"], 30 / 3600)
        self.assertAlmostEqual(crossing["horizon_hours_estimate"], 24 / 3600, places=10)
        self.assertEqual(crossing["bracket_start_hours"], 0)
        self.assertAlmostEqual(crossing["bracket_end_hours"], 30 / 3600)
        self.assertNotIn("capture_completed_utc", summary)
        self.assertFalse((result / "manifest.json").exists())

    def test_censored_horizon_is_unknown_beyond_window(self):
        summary = json.loads((self.run_case() / "summary.json").read_text())
        horizon = summary["horizons"][1]
        self.assertTrue(horizon["right_censored"])
        self.assertIsNone(horizon["first_exceedance_hours"])
        self.assertIsNone(horizon["horizon_hours_estimate"])
        self.assertAlmostEqual(horizon["observed_duration_hours"], 1 / 60)

    def test_initial_exceedance_is_zero_at_actual_epoch(self):
        self.errors = [1.2, 0.2, 0.3]
        summary = json.loads((self.run_case() / "summary.json").read_text())
        self.assertEqual(summary["horizons"][0]["first_exceedance_hours"], 0)
        self.assertEqual(summary["horizons"][0]["horizon_hours_estimate"], 0)

    def test_default_day_grid_has_1441_states_and_exact_endpoints(self):
        target = self.root / "day.csv"
        count = write_predictions(self.satellite, self.origin, 24, 60, target)
        with target.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(count, 1441)
        self.assertEqual(len(rows), 1441)
        self.assertEqual(rows[0]["time_utc"], self.origin.isoformat())
        self.assertEqual(rows[-1]["time_utc"], (self.origin + timedelta(hours=24)).isoformat())

    def test_nondivisible_duration_includes_final_short_interval(self):
        result = self.run_case(duration_hours=65 / 3600, step_seconds=60)
        with (result / "errors.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]["time_utc"], (self.origin + timedelta(seconds=65)).isoformat())

    def test_sources_are_preserved_and_archived_hashes_match(self):
        catalog_hash, reference_hash = digest(self.catalog), digest(self.reference)
        result = self.run_case()
        summary = json.loads((result / "summary.json").read_text())
        self.assertEqual(digest(self.catalog), catalog_hash)
        self.assertEqual(digest(self.reference), reference_hash)
        self.assertEqual(digest(result / "input_catalog.tle"), catalog_hash)
        self.assertEqual(digest(result / summary["references"][0]["archived_file"]), reference_hash)
        for name, expected in summary["output_sha256"].items():
            self.assertEqual(digest(result / name), expected)
        self.assertNotEqual(result, self.run_case())

    def test_wrong_identity_checksum_or_epoch_is_rejected(self):
        with self.assertRaises(ValueError):
            checked_satrec(self.tle, 62261)
        bad = TLEData(self.tle.name, self.tle.line_1[:-1] + "0", self.tle.line_2, self.origin)
        with self.assertRaises(ValueError):
            checked_satrec(bad, CONFIG.norad_id)
        bad = TLEData(self.tle.name, self.tle.line_1, self.tle.line_2, self.origin + timedelta(seconds=1))
        with self.assertRaises(ValueError):
            checked_satrec(bad, CONFIG.norad_id)

    def test_missing_coverage_or_gap_does_not_publish(self):
        with self.assertRaises(ReferenceUnavailable):
            self.run_case(duration_hours=24)
        self.reference.write_text(reference_xml([self.references[0], self.references[-1]]), encoding="utf-8")
        with self.assertRaises(ReferenceUnavailable):
            self.run_case()
        self.assertFalse(self.output.exists())

    def test_wrong_reference_product_is_rejected(self):
        with self.assertRaises(ValueError):
            self.run_case(product="MOE")
        self.assertFalse(self.output.exists())

    def test_invalid_settings_publish_no_results(self):
        for settings in (
            {"duration_hours": 0}, {"duration_hours": float("nan")},
            {"step_seconds": 0}, {"step_seconds": True},
            {"thresholds": ()}, {"thresholds": (1, 1)}, {"thresholds": (float("inf"),)},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.run_case(**settings)
        self.assertFalse(self.output.exists())

    def test_transform_failure_publishes_no_result(self):
        with patch("src.core.historical_validation.transform_predictions", side_effect=ValueError("bad transform")):
            with self.assertRaises(ValueError):
                evaluate_tle("fixture", self.tle, [self.reference], self.output, duration_hours=1 / 60)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_catalog_change_during_evaluation_publishes_no_result(self):
        def mutate(predictions, output):
            self.catalog.write_text(TLE + "\n", encoding="utf-8")
            return self.transform(predictions, output)
        with patch("src.core.historical_validation.transform_predictions", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "catalog changed"):
                evaluate_tle("fixture", self.tle, [self.reference], self.output,
                             duration_hours=1 / 60, step_seconds=30, make_plot=False,
                             source_catalog=self.catalog, expected_catalog_hash=digest(self.catalog))
        self.assertEqual(list(self.output.iterdir()), [])

    def test_plot_is_written(self):
        result = self.run_case(make_plot=True)
        self.assertTrue((result / "error_plot.png").read_bytes().startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
