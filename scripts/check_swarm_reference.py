"""Credential-free live integration check, separate from offline tests.

Checks actual ESA archive discovery, ZIP downloads, metadata, UTC conversion,
units, complete 24-hour coverage and an independent position-only interpolant.
It does not measure TLE prediction accuracy.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import bisect
import json
import math
import tempfile

from src.core.satellites import get_satellite
from src.io.swarm_orbits import download_swarm_orbits, read_swarm_references
from src.core.prospective_validation import interpolate_position

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    for key in ("swarma", "swarmb", "swarmc"):
        config = get_satellite(key)
        paths = download_swarm_orbits(key, date(2026, 9, 2), date(2026, 9, 2), root / key)
        start = datetime(2026, 9, 2, tzinfo=timezone.utc)
        end = start + timedelta(hours=24)
        states, provenance = read_swarm_references(paths, config, start, end)
        times = [state.time for state in states]
        assert states[0].time <= start and states[-1].time >= end
        assert all(6000 < math.sqrt(sum(value * value for value in state.position)) < 8000 for state in states)
        assert all(5 < math.sqrt(sum(value * value for value in state.velocity)) < 10 for state in states)
        samples = [interpolate_position(states, start + timedelta(seconds=60 * i), times) for i in range(1441)]
        assert len(samples) == 1441
        differences = []
        for index in range(10, len(states) - 10, 900):
            timestamp = states[index].time + timedelta(seconds=4.564816)
            selected = states[index - 3:index + 5]
            xs = [(state.time - timestamp).total_seconds() for state in selected]
            independent = [0.0, 0.0, 0.0]
            for i, state in enumerate(selected):
                weight = math.prod(-xs[j] / (xs[i] - xs[j]) for j in range(len(xs)) if j != i)
                for component in range(3):
                    independent[component] += weight * state.position[component]
            differences.append(math.dist(independent, interpolate_position(states, timestamp, times)))
        worst = max(differences)
        assert worst < 0.0001, (key, worst)  # < 10 cm difference between interpolation methods
        print(json.dumps({
            "satellite": key, "downloaded_files": len(paths),
            "reference_states": len(states), "evaluation_samples": len(samples),
            "origin_utc": start.isoformat(), "end_utc": end.isoformat(),
            "reference_frame": provenance[0]["native_frame"],
            "reference_time_system": provenance[0]["native_time_system"],
            "velocity_conversion": provenance[0]["velocity_to_km_s_factor"],
            "maximum_hermite_vs_lagrange_difference_m": worst * 1000,
            "source_hashes": [item["sha256"] for item in provenance],
        }, indent=2))
