from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SatelliteConfig:
    """Configuration for one satellite supported by ORBIT-TRUST."""

    key: str
    name: str
    short_name: str
    norad_id: int
    prospective_collection_enabled: bool = True
    reference_provider: str = "sentinel_eof"
    reference_satellite_id: str = ""


SATELLITES: dict[str, SatelliteConfig] = {
    "sentinel1a": SatelliteConfig(
        key="sentinel1a",
        name="Sentinel-1A",
        short_name="S1A",
        norad_id=39634,
    ),
    "sentinel1b": SatelliteConfig(
        key="sentinel1b",
        name="Sentinel-1B",
        short_name="S1B",
        norad_id=41456,
        prospective_collection_enabled=False,
    ),
    "sentinel1c": SatelliteConfig(
        key="sentinel1c", name="Sentinel-1C", short_name="S1C", norad_id=62261,
    ),
    "swarma": SatelliteConfig(
        key="swarma", name="Swarm-A", short_name="SWA", norad_id=39452,
        reference_provider="swarm_sp3", reference_satellite_id="L47",
    ),
    "swarmb": SatelliteConfig(
        key="swarmb", name="Swarm-B", short_name="SWB", norad_id=39451,
        reference_provider="swarm_sp3", reference_satellite_id="L48",
    ),
    "swarmc": SatelliteConfig(
        key="swarmc", name="Swarm-C", short_name="SWC", norad_id=39453,
        reference_provider="swarm_sp3", reference_satellite_id="L49",
    ),
}


DEFAULT_SATELLITE_KEY = "sentinel1a"


def get_satellite(key: str) -> SatelliteConfig:
    """
    Return the configuration for a supported satellite.
    """

    normalized_key = key.strip().lower()

    try:
        return SATELLITES[normalized_key]
    except KeyError as exc:
        supported = ", ".join(SATELLITES)

        raise ValueError(
            f"Unsupported satellite '{key}'. "
            f"Supported satellites: {supported}"
        ) from exc


def get_default_satellite() -> SatelliteConfig:
    """Return ORBIT-TRUST's default satellite."""

    return get_satellite(DEFAULT_SATELLITE_KEY)
