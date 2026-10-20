from pathlib import Path

from src.io.eof_parser import (
    export_reference_csv,
    parse_eof_file,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

EOF_FOLDER = (
    PROJECT_ROOT
    / "data"
    / "reference"
    / "sentinel1a"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "reference"
    / "sentinel1a"
    / "sentinel1a_precise_raw.csv"
)


def main() -> None:
    eof_files = list(EOF_FOLDER.glob("*.EOF"))

    if not eof_files:
        raise FileNotFoundError(
            f"No EOF files were found in:\n{EOF_FOLDER}"
        )

    eof_file = eof_files[0]

    print(f"Reading:\n{eof_file.name}")

    states = parse_eof_file(eof_file)

    print(f"Parsed states: {len(states)}")
    print(f"First timestamp: {states[0].time}")
    print(f"Last timestamp: {states[-1].time}")
    print(
        "First position, km:",
        states[0].x_km,
        states[0].y_km,
        states[0].z_km,
    )

    export_reference_csv(
        states=states,
        output_path=OUTPUT_FILE,
    )

    print(f"CSV exported to:\n{OUTPUT_FILE}")


if __name__ == "__main__":
    main()