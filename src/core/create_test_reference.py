from __future__ import annotations

import csv
import sys
from pathlib import Path


def create_test_reference(
    source_file: Path,
    output_file: Path,
) -> None:
    with source_file.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as source_csv:
        reader = csv.DictReader(source_csv)
        rows = list(reader)
        fieldnames = reader.fieldnames

    if not rows or fieldnames is None:
        raise ValueError("Source CSV is empty or invalid.")

    for index, row in enumerate(rows):
        growth = index * 0.0005

        row["x_km"] = str(float(row["x_km"]) + 0.010 + growth)
        row["y_km"] = str(float(row["y_km"]) - 0.005 - growth)
        row["z_km"] = str(float(row["z_km"]) + 0.002)

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as output_csv:
        writer = csv.DictWriter(
            output_csv,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(rows)

    print(f"Test reference created:\n{output_file}")


def main() -> None:
    if len(sys.argv) != 3:
        print(
            "Usage:\n"
            "python create_test_reference.py "
            "<source_csv> <output_csv>"
        )
        raise SystemExit(1)

    create_test_reference(
        source_file=Path(sys.argv[1]),
        output_file=Path(sys.argv[2]),
    )


if __name__ == "__main__":
    main()