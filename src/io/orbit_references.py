"""Select a verified reference provider and preserve its native product label."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.core.satellites import SatelliteConfig


def resolve_product(config: SatelliteConfig, product: str = "AUTO") -> str:
    product = product.upper()
    if config.reference_provider == "swarm_sp3":
        if product not in {"AUTO", "PSO"}:
            raise ValueError("Swarm uses precise science orbits (PSO), not Sentinel POE/MOE products.")
        return "PSO"
    if config.reference_provider == "sentinel_eof":
        if product not in {"AUTO", "POE", "MOE"}:
            raise ValueError("Sentinel EOF reference grade must be POE or MOE.")
        return "POE" if product == "AUTO" else product
    raise ValueError("Unsupported reference provider.")


def native_product(config: SatelliteConfig, product: str) -> str:
    product = resolve_product(config, product)
    if config.reference_provider == "swarm_sp3":
        return f"SP3{config.key[-1].upper()}COM_2_"
    return {"POE": "AUX_POEORB", "MOE": "AUX_MOEORB"}[product]


def reference_files(folder: Path, config: SatelliteConfig, product: str) -> list[Path]:
    product = resolve_product(config, product)
    if config.reference_provider == "swarm_sp3":
        from src.io.swarm_orbits import parse_filename, swarm_letter
        selected = []
        for path in Path(folder).rglob("*"):
            if not path.is_file() or path.suffix.upper() != ".ZIP":
                continue
            try:
                info = parse_filename(path.name)
            except ValueError:
                continue
            if info.letter == swarm_letter(config):
                selected.append(path)
        return sorted(selected)
    name = native_product(config, product)
    return sorted(path for path in Path(folder).rglob("*") if path.is_file()
                  and path.suffix.upper() == ".EOF"
                  and path.name.startswith(config.short_name + "_") and f"_{name}_" in path.name)


def load_reference_states(
    paths: list[Path], config: SatelliteConfig, product: str,
    origin: datetime, end: datetime, *, max_gap_seconds: float = 20,
):
    product = resolve_product(config, product)
    if config.reference_provider == "swarm_sp3":
        from src.io.swarm_orbits import read_swarm_references
        return read_swarm_references(paths, config, origin, end, max_gap_seconds=max_gap_seconds)
    from src.core.prospective_validation import read_references
    return read_references(paths, config.name, product, origin, end, max_gap_seconds=max_gap_seconds)


def overlap_description(config: SatelliteConfig) -> str:
    if config.reference_provider == "swarm_sp3":
        return "Highest processing baseline, then latest Creation_Date wins at overlapping state timestamps"
    return "Latest Creation_Date wins at each overlapping reference timestamp"
