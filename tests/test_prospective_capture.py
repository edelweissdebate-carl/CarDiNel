"""Offline checks use a synthetic provider; no Space-Track credentials are used."""
import csv
import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from sgp4.api import Satrec, jday

from src.core.prospective_capture import capture_forecast
from src.core.satellites import SatelliteConfig, get_satellite
from src.io.spacetrack_client import SpaceTrackClient, SpaceTrackError


TLE = (
    "1 25544U 98067A   19343.69339541  .00001764  00000-0  38792-4 0  9991\n"
    "2 25544  51.6439 211.2001 0007417  17.6667  85.6398 15.50103472202482\n"
)
BASE = datetime(2019, 12, 10, tzinfo=timezone.utc)
FIXTURE = SatelliteConfig("fixture", "Test fixture", "TEST", 25544)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry = patch("src.core.prospective_capture.get_satellite", return_value=FIXTURE)
        self.registry.start()
        self.addCleanup(self.registry.stop)

    def capture(self, **kwargs):
        options = dict(duration_hours=1 / 60, step_seconds=40,
                       output_root=self.root, clock=lambda: BASE)
        options.update(kwargs)
        return capture_forecast("fixture", lambda identifier: TLE, **options)

    def test_future_origin_states_and_hashes(self):
        folder = self.capture()
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual((folder / "source_response.tle").read_bytes(), TLE.encode())
        self.assertEqual(manifest["status"], "pending_poe_validation")
        self.assertEqual(manifest["prediction_frame"], "TEME")
        self.assertNotIn("reliability_horizon", manifest)
        self.assertEqual(manifest["norad_id"], 25544)
        for filename, digest in manifest["sha256"].items():
            self.assertEqual(hashlib.sha256((folder / filename).read_bytes()).hexdigest(), digest)
        with (folder / "predictions_teme.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 3)
        origin = BASE + timedelta(seconds=300)
        self.assertEqual(datetime.fromisoformat(rows[0]["time_utc"]), origin)
        self.assertEqual(datetime.fromisoformat(rows[-1]["time_utc"]), origin + timedelta(seconds=60))
        satellite = Satrec.twoline2rv(*TLE.splitlines())
        jd, fraction = jday(2019, 12, 10, 0, 5, 0)
        error, position, velocity = satellite.sgp4(jd, fraction)
        self.assertEqual(error, 0)
        for field, expected in zip(("x_km", "y_km", "z_km", "vx_km_s", "vy_km_s", "vz_km_s"),
                                   (*position, *velocity)):
            self.assertAlmostEqual(float(rows[0][field]), expected, places=9)

    def test_repeated_input_does_not_overwrite_forecast(self):
        first = self.capture()
        initial_bytes = (first / "manifest.json").read_bytes()
        second = self.capture()
        self.assertNotEqual(first, second)
        self.assertEqual((first / "manifest.json").read_bytes(), initial_bytes)

    def test_late_generation_is_not_published(self):
        ticks = iter((BASE, BASE, BASE, BASE + timedelta(seconds=300)))
        with self.assertRaisesRegex(RuntimeError, "first forecast time"):
            self.capture(clock=lambda: next(ticks))
        self.assertEqual(list((self.root / "fixture").iterdir()), [])

    def test_late_finalization_is_not_published(self):
        ticks = iter((BASE, BASE, BASE, BASE, BASE + timedelta(seconds=300)))
        with self.assertRaisesRegex(RuntimeError, "finalized"):
            self.capture(clock=lambda: next(ticks))
        self.assertEqual(list((self.root / "fixture").iterdir()), [])

    def test_wrong_satellite_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "identifiers"):
            with patch("src.core.prospective_capture.get_satellite", return_value=get_satellite("sentinel1a")):
                self.capture()

    def test_historical_only_satellite_is_rejected_before_fetch(self):
        fetcher = Mock(return_value=TLE)
        with patch("src.core.prospective_capture.get_satellite", return_value=get_satellite("sentinel1b")):
            with self.assertRaisesRegex(ValueError, "historical"):
                capture_forecast("sentinel1b", fetcher, output_root=self.root)
        fetcher.assert_not_called()

    def test_invalid_settings_are_rejected(self):
        for options in ({"duration_hours": float("nan")}, {"duration_hours": float("inf")},
                        {"duration_hours": 0}, {"step_seconds": 0}, {"step_seconds": 0.5},
                        {"lead_seconds": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.capture(**options)

    def test_naive_and_backwards_clocks_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.capture(clock=lambda: BASE.replace(tzinfo=None))
        ticks = iter((BASE, BASE - timedelta(seconds=1)))
        with self.assertRaisesRegex(ValueError, "backwards"):
            self.capture(clock=lambda: next(ticks))


class CurrentGPTests(unittest.TestCase):
    def setUp(self):
        self.client = SpaceTrackClient("fixture-user", "fixture-password")
        self.addCleanup(self.client.close)
        self.client.session.get = Mock(return_value=Mock(status_code=200, text=TLE))

    def test_current_query_and_original_response(self):
        self.assertEqual(self.client.fetch_current_tle(25544), TLE)
        self.client.session.get.assert_called_once_with(
            "https://www.space-track.org/basicspacedata/query/class/gp/NORAD_CAT_ID/25544/format/tle",
            timeout=60,
        )

    def test_history_query_is_preserved(self):
        query = self.client.build_history_query(25544, date(2021, 1, 1), date(2021, 1, 2))
        self.assertIn("/class/gp_history/", query)
        self.assertIn("/EPOCH/2021-01-01--2021-01-02/", query)

    def test_invalid_current_responses_are_rejected(self):
        invalid_checksum = TLE.replace("9991", "9990")
        for text in ("", "<html>Login</html>", TLE + TLE, invalid_checksum,
                     TLE.replace("25544", "39634")):
            with self.subTest(text=text):
                self.client.session.get.return_value = Mock(status_code=200, text=text)
                with self.assertRaises(SpaceTrackError):
                    self.client.fetch_current_tle(25544)

    def test_http_failure_is_reported(self):
        self.client.session.get.return_value = Mock(status_code=403, text="Forbidden")
        with self.assertRaisesRegex(SpaceTrackError, "403"):
            self.client.fetch_current_tle(25544)

    def test_invalid_identifiers_are_rejected_before_network(self):
        for identifier in (0, -1, 100000, 25544.5, True):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.client.fetch_current_tle(identifier)
        self.client.session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
