from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SatelliteConfig:
    """Configuration for one satellite supported by ORBIT-TRUST."""

    key: str
    name: str
    short_name: str
    norad_id: int


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