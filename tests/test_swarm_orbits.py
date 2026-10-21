"""Offline Swarm checks use synthetic SP3 references and mocked HTTP responses."""
import csv
import json
import math
import tempfile
import unittest
import zipfile
import requests
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from sgp4.api import Satrec

from src.core.historical_validation import evaluate_tle
from src.core.propagator import tle_epoch_datetime
from src.core.prospective_capture import capture_forecast
from src.core.prospective_validation import ReferenceUnavailable, digest, validate_capture
from src.core.satellites import get_satellite
from src.io.orbit_references import resolve_product, native_product, reference_files
from src.io.swarm_orbits import (
    SwarmProduct, discover_swarm_products, download_swarm_orbits, parse_filename,
    parse_sp3, read_swarm_product, read_swarm_references,
)
from src.io.tle_reader import TLEData

GPS_START = datetime(2026, 9, 1)
BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def utc_gps(calendar):
    # Independent fixture rule for dates tested here: 17 s in 2016; 18 s in 2026.
    offset = 17 if calendar.year == 2016 else 18
    return calendar.replace(tzinfo=timezone.utc) - timedelta(seconds=offset)


def sp3_text(start=GPS_START, count=9, interval=10, satellite="L47", frame="IGc20", time_system="GPS", shift=0):
    header = (
        f"#dV{start.year:4d} {start.month:2d} {start.day:2d} {start.hour:2d} "
        f"{start.minute:2d} {start.second:11.8f} {count:7d} U+u   {frame:5s} FIT TUD\n"
        f"## 2434 259200.00000000 {interval:13.8f} 61284 0.0000000000000\n"
        f"+    1   {satellite}\n%c M  cc {time_system} ccc cccc\n"
    )
    lines = [header]
    for index in range(count):
        stamp = start + timedelta(seconds=index * interval)
        t = index * interval
        # An analytic cubic trajectory makes velocity-unit conversion observable.
        position = (7000 + shift + t**3 * 1e-6, 2 + t**2 * 1e-4, 3.0)
        velocity = (3 * t**2 * 1e-6, 2 * t * 1e-4, 0.0)
        lines.append(stamp.strftime("*  %Y %m %d %H %M ") + f"{stamp.second:011.8f}\n")
        lines.append("P" + satellite + "".join(f"{value:14.7f}" for value in position) + " 999999.999999\n")
        lines.append("V" + satellite + "".join(f"{value * 10000:14.7f}" for value in velocity) + " 999999.999999\n")
    lines.append("EOF\n")
    return "".join(lines)


def product_bytes(letter="A", start=GPS_START, count=9, interval=10, baseline=203, shift=0, edits=None):
    identity = {"A": "L47", "B": "L48", "C": "L49"}[letter]
    first = utc_gps(start)
    stop = first + timedelta(seconds=count * interval)
    stem = f"SW_OPER_SP3{letter}COM_2__{first:%Y%m%dT%H%M%S}_{stop:%Y%m%dT%H%M%S}_{baseline:04d}"
    header = (
        "<Earth_Explorer_Header><Fixed_Header>"
        f"<Mission>Swarm</Mission><File_Name>{stem}</File_Name>"
        f"<File_Type>SP3{letter}COM_2_</File_Type><File_Version>{baseline:04d}</File_Version>"
        f"<Validity_Start>UTC={first.isoformat()}</Validity_Start>"
        f"<Validity_Stop>UTC={stop.isoformat()}</Validity_Stop>"
        f"<Creation_Date>UTC={(stop + timedelta(days=21)).isoformat()}</Creation_Date>"
        "</Fixed_Header><Variable_Header><Product_Err>0</Product_Err>"
        "<Quality_Indicator>000</Quality_Indicator></Variable_Header></Earth_Explorer_Header>"
    )
    text = sp3_text(start, count, interval, identity, shift=shift)
    if edits:
        header, text = edits(header, text)
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(stem + ".sp3", text)
        archive.writestr(stem + ".HDR", header)
    return stem + ".ZIP", stream.getvalue()


def tle_fixture():
    lines = [
        "1 25544U 98067A   19343.69339541  .00001764  00000-0  38792-4 0  9991",
        "2 25544  51.6439 211.2001 0007417  17.6667  85.6398 15.50103472202482",
    ]
    lines = [line[:2] + "39452" + line[7:] for line in lines]
    lines[0] = lines[0][:18] + "26244.00000000" + lines[0][32:]
    for index, line in enumerate(lines):
        checksum = sum(int(char) if char.isdigit() else 1 if char == "-" else 0 for char in line[:68]) % 10
        lines[index] = line[:68] + str(checksum)
    return "\n".join(lines) + "\n"


class Response:
    def __init__(self, payload=None, content=b"", fail=False):
        self.payload, self.content, self.fail = payload, content, fail
    def raise_for_status(self):
        if self.fail:
            raise RuntimeError("HTTP fixture failure")
    def json(self):
        return self.payload
    def iter_content(self, chunk_size):
        yield self.content
    def close(self):
        pass


class Session:
    def __init__(self, products, content):
        self.products, self.content, self.calls = products, content, []
    def get(self, url, params, **kwargs):
        self.calls.append(params)
        if params["do"] == "download":
            return Response(content=self.content)
        size, pos = params["maxfiles"], params["pos"]
        return Response({"success": True, "results": self.products[pos:pos + size]})


class SwarmTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = get_satellite("swarma")
        self.name, self.data = product_bytes()
        self.path = self.root / self.name
        self.path.write_bytes(self.data)

    def test_registry_and_product_grades(self):
        for key, norad, code in (("swarma", 39452, "L47"), ("swarmb", 39451, "L48"), ("swarmc", 39453, "L49")):
            config = get_satellite(key)
            self.assertEqual((config.norad_id, config.reference_satellite_id), (norad, code))
            self.assertEqual(resolve_product(config), "PSO")
            self.assertEqual(native_product(config, "PSO"), f"SP3{key[-1].upper()}COM_2_")
            self.assertTrue(config.prospective_collection_enabled)
        with self.assertRaises(ValueError):
            resolve_product(self.config, "POE")
        with self.assertRaises(ValueError):
            resolve_product(get_satellite("sentinel1a"), "PSO")

    def test_fixed_width_negative_values_gps_time_and_velocity_units(self):
        states, metadata = parse_sp3(sp3_text(), "L47")
        self.assertEqual(states[0].time, BASE - timedelta(seconds=18))
        self.assertEqual(states[2].position, (7000.008, 2.04, 3.0))
        self.assertAlmostEqual(states[2].velocity[0], 0.0012)
        self.assertAlmostEqual(states[2].velocity[1], 0.004)
        self.assertEqual(metadata["native_frame"], "IGc20")
        text = sp3_text().replace("  7000.0000000", " -7000.0000000")
        negative, _ = parse_sp3(text, "L47")
        self.assertEqual(negative[0].position[0], -7000)

    def test_gps_offset_changes_with_historical_leap_seconds(self):
        states, _ = parse_sp3(sp3_text(start=datetime(2016, 1, 1)), "L47")
        self.assertEqual(states[0].time, datetime(2015, 12, 31, 23, 59, 43, tzinfo=timezone.utc))

    def test_unknown_frame_time_system_identity_and_nonfinite_states_are_rejected(self):
        cases = [sp3_text(frame="TEME"), sp3_text(time_system="UTC"), sp3_text(satellite="L48"),
                 sp3_text().replace("  7000.0000000", "           nan")]
        for text in cases:
            with self.subTest(header=text.splitlines()[0]), self.assertRaises(ValueError):
                parse_sp3(text, "L47")

    def test_missing_velocity_duplicate_state_truncation_and_count_are_rejected(self):
        text = sp3_text()
        velocity = next(line for line in text.splitlines() if line.startswith("V"))
        position = next(line for line in text.splitlines() if line.startswith("P"))
        bad_cases = [
            text.replace(velocity + "\n", "", 1),
            text.replace(position + "\n", position + "\n" + position + "\n", 1),
            text.removesuffix("EOF\n"),
            text.replace("      9 U+u", "     10 U+u", 1),
        ]
        for bad in bad_cases:
            with self.subTest(bad=bad[:70]), self.assertRaises(ValueError):
                parse_sp3(bad, "L47")

    def test_zip_header_and_archive_hashes(self):
        states, metadata = read_swarm_product(self.path, self.config)
        self.assertEqual(len(states), 9)
        self.assertEqual(metadata["sha256"], digest(self.path))
        self.assertEqual(metadata["reference_grade"], "PSO")
        self.assertEqual(metadata["actual_last_state_utc"], states[-1].time.isoformat())
        self.assertEqual(reference_files(self.root, self.config, "AUTO"), [self.path])

    def test_wrong_product_quality_validity_or_mission_header_is_rejected(self):
        edits = [
            lambda header: header.replace("<Mission>Swarm", "<Mission>Other"),
            lambda header: header.replace("<File_Type>SP3ACOM", "<File_Type>SP3BCOM"),
            lambda header: header.replace("<Product_Err>0", "<Product_Err>1"),
            lambda header: header.replace("<Quality_Indicator>000", "<Quality_Indicator>001"),
            lambda header: header.replace("2026-08-31T23:59:42", "2026-08-31T23:59:41"),
        ]
        for edit in edits:
            name, data = product_bytes(edits=lambda h, s: (edit(h), s))
            self.path.write_bytes(data)
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                read_swarm_product(self.path, self.config)
        with self.assertRaises(ValueError):
            read_swarm_product(self.path, get_satellite("swarmb"))

    def test_extra_archive_member_is_rejected_without_extraction(self):
        with zipfile.ZipFile(self.path, "a") as archive:
            archive.writestr("../outside.txt", "bad")
        with self.assertRaises(ValueError):
            read_swarm_product(self.path, self.config)
        self.assertFalse((self.root.parent / "outside.txt").exists())

    def test_reference_coverage_gaps_and_baseline_priority(self):
        start = BASE - timedelta(seconds=18)
        lower_name, lower_data = product_bytes(baseline=201, shift=1)
        lower = self.root / lower_name
        lower.write_bytes(lower_data)
        states, provenance = read_swarm_references([self.path, lower], self.config, start, start + timedelta(seconds=60))
        self.assertEqual(states[0].position[0], 7000)
        self.assertEqual(len(provenance), 2)
        with self.assertRaises(ReferenceUnavailable):
            read_swarm_references([self.path], self.config, start, start + timedelta(seconds=90))
        gap_name, gap_data = product_bytes(interval=30, count=4)
        gap = self.root / gap_name
        gap.write_bytes(gap_data)
        with self.assertRaises(ReferenceUnavailable):
            read_swarm_references([gap], self.config, start, start + timedelta(seconds=60))

    def entries(self):
        info = parse_filename(self.name)
        return [{"name": info.filename, "path": info.archive_path, "is_dir": False, "is_readable": True}]

    def test_paginated_discovery_checks_paths_and_selects_latest_baseline(self):
        entry = self.entries()[0]
        old = dict(entry)
        old["name"] = old["name"].replace("_0203.ZIP", "_0201.ZIP")
        old["path"] = old["path"].replace("_0203.ZIP", "_0201.ZIP")
        session = Session([old, entry], self.data)
        products = discover_swarm_products("swarma", BASE, BASE + timedelta(seconds=30), session=session, page_size=1)
        self.assertEqual([product.filename for product in products], [self.name])
        self.assertEqual(len(session.calls), 3)
        bad = dict(entry, path="../bad")
        with self.assertRaises(ValueError):
            discover_swarm_products("swarma", BASE, BASE, session=Session([bad], b""))

    def test_download_validates_before_publish_and_reuses_cache(self):
        destination = self.root / "downloads"
        session = Session(self.entries(), self.data)
        paths = download_swarm_orbits("swarma", BASE.date(), BASE.date(), destination, session=session)
        self.assertEqual(len(paths), 1)
        expected = digest(paths[0])
        download_swarm_orbits("swarma", BASE.date(), BASE.date(), destination, session=session)
        self.assertEqual(digest(paths[0]), expected)
        self.assertEqual(sum(call["do"] == "download" for call in session.calls), 1)
        invalid = Session(self.entries(), b"not a zip")
        with self.assertRaises((ValueError, zipfile.BadZipFile)):
            download_swarm_orbits("swarma", BASE.date(), BASE.date(), self.root / "invalid", session=invalid)
        self.assertEqual(list((self.root / "invalid").glob("*.ZIP")), [])

    def test_interrupted_download_restarts_and_publishes_only_complete_zip(self):
        class Interrupted(Response):
            def iter_content(self, chunk_size):
                yield b"partial transfer that must be discarded"
                raise requests.exceptions.ChunkedEncodingError("interrupted fixture")
        session = Session(self.entries(), self.data)
        responses = [Interrupted(), Response(content=self.data)]
        original_get = session.get
        def get(url, params, **kwargs):
            if params["do"] == "download":
                session.calls.append(params)
                return responses.pop(0)
            return original_get(url, params, **kwargs)
        session.get = get
        destination = self.root / "retried"
        with patch("src.io.swarm_orbits.sleep"):
            paths = download_swarm_orbits("swarma", BASE.date(), BASE.date(), destination, session=session)
        self.assertEqual(paths[0].read_bytes(), self.data)
        self.assertEqual(len(responses), 0)
        self.assertEqual(list(destination.glob(".partial-*")), [])

    def test_repeated_stream_failure_is_bounded_and_publishes_no_zip(self):
        class Interrupted(Response):
            def iter_content(self, chunk_size):
                yield b"partial"
                raise requests.exceptions.ChunkedEncodingError("interrupted fixture")
        session = Session(self.entries(), self.data)
        original_get = session.get
        def get(url, params, **kwargs):
            if params["do"] == "download":
                session.calls.append(params)
                return Interrupted()
            return original_get(url, params, **kwargs)
        session.get = get
        destination = self.root / "failed-retries"
        with patch("src.io.swarm_orbits.sleep"), self.assertRaises(requests.exceptions.ChunkedEncodingError):
            download_swarm_orbits("swarma", BASE.date(), BASE.date(), destination, session=session)
        self.assertEqual(sum(call["do"] == "download" for call in session.calls), 3)
        self.assertEqual(list(destination.iterdir()), [])

    def test_historical_and_prospective_paths_use_swarm_references(self):
        tle_text = tle_fixture()
        lines = tle_text.splitlines()
        tle = TLEData("fixture", *lines, tle_epoch_datetime(Satrec.twoline2rv(*lines)))
        self.assertEqual(tle.epoch, BASE)
        def transform(predictions, output):
            (output / "earth_orientation.ecsv").write_text("fixture", encoding="utf-8")
            # Return fixed positions: this test checks routing, not physical accuracy.
            return [(7000, 2, 3) for state in predictions], {"method": "fixture"}
        name, data = product_bytes(start=datetime(2026, 9, 1, 0, 0, 8), count=10)
        path = self.root / name
        path.write_bytes(data)
        with patch("src.core.historical_validation.transform_predictions", side_effect=transform):
            result = evaluate_tle("swarma", tle, [path], self.root / "historical",
                                  duration_hours=1 / 60, step_seconds=30, make_plot=False)
        summary = json.loads((result / "summary.json").read_text())
        self.assertEqual(summary["reference_grade"], "PSO")
        self.assertEqual(summary["reference_product"], "SP3ACOM_2_")
        self.assertEqual(summary["sample_count"], 3)
        capture = capture_forecast("swarma", lambda _: tle_text, output_root=self.root / "captures",
                                   duration_hours=1 / 60, step_seconds=30, lead_seconds=10, clock=lambda: BASE)
        with patch("src.core.prospective_validation.transform_predictions", side_effect=transform):
            result = validate_capture(capture, [path], self.root / "prospective", make_plot=False)
        summary = json.loads((result / "summary.json").read_text())
        self.assertEqual(summary["reference_grade"], "PSO")
        self.assertEqual(summary["reference_product"], "SP3ACOM_2_")
        self.assertTrue((capture / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
