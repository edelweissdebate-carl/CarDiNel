# CarDiNel Trust

CarDiNel Trust builds on the uploaded CarDiJun / ORBIT-TRUST Python project.
The existing research pipeline reconstructs historical TLE predictions and
compares them with precise orbit ephemerides (POE). The first new capability
is prospective forecast capture. Multi-mission POE acquisition and delayed
validation of these captures are subsequent integration steps.

## Capture a new forecast

Run from the project root in the existing Python environment. The command
uses the project's existing `sgp4`, `requests`, and `python-dotenv` dependencies.
Keep these credentials in your local `.env` file:

```dotenv
SPACETRACK_USERNAME=your_space_track_username
SPACETRACK_PASSWORD=your_space_track_password
```

Do not commit `.env`. It is ignored by Git and is not copied into captures.

```powershell
python -m src.core.prospective_capture --satellite sentinel1a --duration-hours 24 --step-seconds 60 --lead-seconds 300
```

The command logs in to Space-Track and requests the **current GP record**.
It does not reuse the historical TLE cache or select a record from GP history.
The first forecast timestamp is five minutes after generation starts by
default. Increase `--lead-seconds` if the computer needs more time.

Each invocation creates its own directory under
`data/sentinel1a/prospective/` containing:

- `source_response.tle`: the original current-GP response text.
- `predictions_teme.csv`: fixed SGP4 states in **TEME**, in km and km/s.
- `manifest.json`: satellite identity, acquisition and generation timestamps,
  TLE epoch and age, forecast origin and end, sampling settings, software
  information, source hashes, and SHA-256 hashes of the two data files.

Both forecast endpoints are included; a shorter final interval is used when
the requested duration is not an exact multiple of the sampling interval.
Captures are finalized only when generation completes before the first
forecast timestamp. Failed generations are not published as valid captures.
Repeated collection of the same TLE produces separate archives; those
archives must not automatically be counted as independent statistical cases.

All timestamps use UTC. Keep the computer's clock synchronized. The timing
checks assume a trustworthy system clock. Hashes detect changes to saved data
when compared with a trusted manifest; they are not an external timestamp or
protection against deliberate rewriting of both data and manifest.

## What the capture does and does not establish

A successful archive has `status: pending_poe_validation`. It contains no
measured prediction errors or reliability horizons. Those require the
corresponding POE data after publication. Its TEME states must first be
transformed into the reference frame at the saved forecast timestamps.
The saved forecasts must not be regenerated or replaced during validation.

This initial command collects Sentinel-1A. Sentinel-1B remains available to
the historical workflow, but is disabled for prospective collection because
its operational mission has ended. Extending the registry alone does not
provide POE support for a different mission. A verified reference provider,
format, coordinate system, and time system are needed before enabling that
mission in the full research pipeline.

The historical calculations and GUI remain unchanged in this update.
In particular, changing historical date arguments to recent dates does not
make that pipeline a prospective experiment.

## Offline checks

```powershell
python -m unittest discover -s tests -v
```

These tests use a fixed TLE fixture and mocked HTTP responses. They check
forecast timing, SGP4 state export, input identity and checksums, archive
isolation, and rejection of late captures. They do not certify live
Space-Track access or scientific agreement with POE reference trajectories.

## Sources

- Space-Track API documentation: https://www.space-track.org/documentation
- Sentinel-1B end of mission: https://www.esa.int/Applications/Observing_the_Earth/Copernicus/Sentinel-1/Mission_ends_for_Copernicus_Sentinel-1B_satellite
