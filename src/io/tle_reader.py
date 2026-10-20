from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sgp4.api import Satrec

from src.core.propagator import tle_epoch_datetime


@dataclass
class TLEData:
    name: str
    line_1: str
    line_2: str
    epoch: datetime


def load_tle_file(file_path: str | Path) -> TLEData:
    """
    Load the first valid, unique TLE entry from a file.
    """
    entries = load_tle_catalog(file_path)

    if not entries:
        raise ValueError("No valid TLE entries were found.")

    return entries[0]


def deduplicate_tle_entries(
    entries: list[TLEData],
) -> list[TLEData]:
    """
    Remove duplicate TLE observations that share the same epoch.

    The first TLE encountered for an epoch is retained. If a later
    entry has the same epoch but different TLE lines, it is still
    excluded so that one epoch cannot be counted twice as an
    independent experimental case.
    """
    unique_by_epoch: dict[datetime, TLEData] = {}
    exact_duplicates = 0
    conflicting_same_epoch = 0

    for entry in entries:
        existing = unique_by_epoch.get(entry.epoch)

        if existing is None:
            unique_by_epoch[entry.epoch] = entry
            continue

        if (
            existing.line_1 == entry.line_1
            and existing.line_2 == entry.line_2
        ):
            exact_duplicates += 1
        else:
            conflicting_same_epoch += 1

    unique_entries = sorted(
        unique_by_epoch.values(),
        key=lambda entry: entry.epoch,
    )

    removed = exact_duplicates + conflicting_same_epoch

    if removed:
        details = []

        if exact_duplicates:
            details.append(
                f"{exact_duplicates} exact duplicate"
                f"{'s' if exact_duplicates != 1 else ''}"
            )

        if conflicting_same_epoch:
            details.append(
                f"{conflicting_same_epoch} same-epoch revision"
                f"{'s' if conflicting_same_epoch != 1 else ''}"
            )

        warnings.warn(
            "TLE catalog deduplication removed "
            f"{removed} entr{'ies' if removed != 1 else 'y'} "
            f"({', '.join(details)}). "
            "Only one TLE per epoch is retained for analysis.",
            RuntimeWarning,
            stacklevel=2,
        )

    return unique_entries


def load_tle_catalog(
    file_path: str | Path,
) -> list[TLEData]:
    """
    Load every valid TLE entry from a file and remove repeated epochs.

    Supported formats:

    Two-line format:
        1 ...
        2 ...

    Three-line format:
        SATELLITE NAME
        1 ...
        2 ...
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"TLE file was not found:\n{path}"
        )

    lines = [
        line.strip()
        for line in path.read_text(
            encoding="utf-8-sig"
        ).splitlines()
        if line.strip()
    ]

    if not lines:
        raise ValueError("The TLE file is empty.")

    entries: list[TLEData] = []
    index = 0
    unnamed_number = 1

    while index < len(lines):
        current_line = lines[index]

        if current_line.startswith("1 "):
            name = f"Satellite TLE {unnamed_number}"
            line_1 = current_line

            if index + 1 >= len(lines):
                raise ValueError(
                    "A TLE Line 1 was found without a following Line 2."
                )

            line_2 = lines[index + 1]

            if not line_2.startswith("2 "):
                raise ValueError(
                    f"Expected TLE Line 2 after:\n{line_1}"
                )

            index += 2
            unnamed_number += 1

        else:
            name = current_line

            if index + 2 >= len(lines):
                raise ValueError(
                    f"Incomplete TLE entry after satellite name: {name}"
                )

            line_1 = lines[index + 1]
            line_2 = lines[index + 2]

            if not line_1.startswith("1 "):
                raise ValueError(
                    f"Expected TLE Line 1 after satellite name: {name}"
                )

            if not line_2.startswith("2 "):
                raise ValueError(
                    f"Expected TLE Line 2 after satellite name: {name}"
                )

            index += 3

        satellite = Satrec.twoline2rv(
            line_1,
            line_2,
        )

        epoch = tle_epoch_datetime(satellite)

        entries.append(
            TLEData(
                name=name,
                line_1=line_1,
                line_2=line_2,
                epoch=epoch,
            )
        )

    entries.sort(
        key=lambda entry: entry.epoch
    )

    return deduplicate_tle_entries(entries)