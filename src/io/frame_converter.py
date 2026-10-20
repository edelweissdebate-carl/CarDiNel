from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import astropy.units as u
from astropy.coordinates import (
    CartesianDifferential,
    CartesianRepresentation,
    ITRS,
    TEME,
)
from astropy.time import Time


@dataclass
class EarthFixedState:
    time: datetime
    x_km: float
    y_km: float
    z_km: float
    vx_km_s: float
    vy_km_s: float
    vz_km_s: float


def teme_to_itrs(
    time: datetime,
    position_km: tuple[float, float, float],
    velocity_km_s: tuple[float, float, float],
) -> EarthFixedState:
    position = CartesianRepresentation(
        position_km[0] * u.km,
        position_km[1] * u.km,
        position_km[2] * u.km,
    )

    velocity = CartesianDifferential(
        velocity_km_s[0] * u.km / u.s,
        velocity_km_s[1] * u.km / u.s,
        velocity_km_s[2] * u.km / u.s,
    )

    teme = TEME(
        position.with_differentials(velocity),
        obstime=Time(time),
    )

    itrs = teme.transform_to(
        ITRS(obstime=Time(time))
    )

    cartesian = itrs.cartesian
    differential = cartesian.differentials["s"]

    return EarthFixedState(
        time=time,
        x_km=cartesian.x.to_value(u.km),
        y_km=cartesian.y.to_value(u.km),
        z_km=cartesian.z.to_value(u.km),
        vx_km_s=differential.d_x.to_value(u.km / u.s),
        vy_km_s=differential.d_y.to_value(u.km / u.s),
        vz_km_s=differential.d_z.to_value(u.km / u.s),
    )