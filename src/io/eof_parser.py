from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class PreciseOrbitState:
    time: datetime
    x_km: float
    y_km: float
    z_km: float
    vx_km_s: float
    vy_km_s: float
    vz_km_s: float


def parse_utc(value: str) -> datetime:
    """
    Parse Sentinel EOF UTC strings such as:
    UTC=2024-01-01T00:00:00.000000
    """
    cleaned = value.strip()

    if cleaned.startswith("UTC="):
        cleaned = cleaned[4:]

    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"

    parsed = datetime.fromisoformat(cleaned)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def element_text(
    parent: ET.Element,
    tag_name: str,
) -> str:
    """
    Find an XML child by its local tag name.
    This works even when the file uses XML namespaces.
    """
    for child in parent.iter():
        local_name = child.tag.split("}")[-1]

        if local_name == tag_name:
            if child.text is None:
                raise ValueError(f"Empty XML field: {tag_name}")

            return child.text.strip()

    raise ValueError(f"Missing XML field: {tag_name}")


def parse_eof_file(
    file_path: str | Path,
) -> list[PreciseOrbitState]:
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"EOF file was not found:\n{path}"
        )

    try:
        tree = ET.parse(path)
    except ET.ParseError as error:
        raise ValueError(
            f"The selected file is not valid XML: {error}"
        ) from error

    root = tree.getroot()
    states: list[PreciseOrbitState] = []

    for element in root.iter():
        if element.tag.split("}")[-1] != "OSV":
            continue

        try:
            time = parse_utc(
                element_text(element, "UTC")
            )

            # Sentinel EOF position values are stored in metres.
            x_km = float(element_text(element, "X")) / 1000.0
            y_km = float(element_text(element, "Y")) / 1000.0
            z_km = float(element_text(element, "Z")) / 1000.0

            # Velocity values are stored in metres per second.
            vx_km_s = (
                float(element_text(element, "VX")) / 1000.0
            )
            vy_km_s = (
                float(element_text(element, "VY")) / 1000.0
            )
            vz_km_s = (
                float(element_text(element, "VZ")) / 1000.0
            )

            states.append(
                PreciseOrbitState(
                    time=time,
                    x_km=x_km,
                    y_km=y_km,
                    z_km=z_km,
                    vx_km_s=vx_km_s,
                    vy_km_s=vy_km_s,
                    vz_km_s=vz_km_s,
                )
            )

        except Exception as error:
            raise ValueError(
                f"Failed to parse an OSV entry: {error}"
            ) from error

    if not states:
        raise ValueError(
            "No orbit state vectors were found in the EOF file."
        )

    states.sort(key=lambda state: state.time)

    return states


def export_reference_csv(
    states: list[PreciseOrbitState],
    output_path: str | Path,
) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as csv_file:
        writer = csv.writer(csv_file)

        writer.writerow(
            [
                "time_utc",
                "x_km",
                "y_km",
                "z_km",
                "vx_km_s",
                "vy_km_s",
                "vz_km_s",
            ]
        )

        for state in states:
            writer.writerow(
                [
                    state.time.isoformat(),
                    state.x_km,
                    state.y_km,
                    state.z_km,
                    state.vx_km_s,
                    state.vy_km_s,
                    state.vz_km_s,
                ]
            )