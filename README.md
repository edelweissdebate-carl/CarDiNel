# CarDiNel Trust

CarDiNel extends the CarDiJun / ORBIT-TRUST project with prospective
forecast capture and delayed validation of those fixed forecasts.

## 1. Capture current forecasts

Run from the project root using the existing Python environment.
Keep Space-Track credentials in the local, ignored `.env`:

```dotenv
SPACETRACK_USERNAME=your_space_track_username
SPACETRACK_PASSWORD=your_space_track_password
```

```powershell
python -m src.core.prospective_capture --satellite sentinel1a
python -m src.core.prospective_capture --satellite sentinel1c
```

Sentinel-1C uses NORAD catalog ID **62261**. Sentinel-1B remains historical only.
Adding Sentinel-1C enables the new capture and validation commands; the legacy
GUI and historical report pipeline still expose their original satellite choices.

Each capture creates a new directory under `data/<satellite>/prospective/`:
original current-GP TLE response, fixed SGP4 TEME states, and a manifest
with timestamps, satellite identity, software details, and file hashes.
Defaults: 24 hours, 60-second samples, five-minute lead. Both endpoints are saved.
Increase `--lead-seconds` if generation is slow.

Captures finishing at or after their first forecast time are rejected.
Never replace a saved forecast during validation. Keep the clock synchronized.
Local timestamps and hashes support auditing but are not an independent
timestamp certification or proof against deliberate rewriting of the manifest.

## 2. Validate a capture when reference files are available

The existing environment also needs `astropy`, `numpy`, and `matplotlib`
(already listed in the project requirements). Obtain Sentinel EOF files
covering the **complete forecast interval**, including both endpoints.

Final Sentinel-1 POE distribution has about 21 days of latency. MOE is listed
with about two days. An October 20–21 forecast cannot have final POE
validation by October 26. A successful capture alone is not an accuracy result.

Place downloaded reference files in a directory such as `data/references`.
Reference retrieval for MOE is currently manual through Copernicus Data Space;
this update does not add automatic MOE downloading or reuse Space-Track
credentials for Copernicus.

```powershell
python -m src.core.prospective_validation --capture "data/sentinel1a/prospective/YOUR_CAPTURE_FOLDER" --reference-dir "data/references" --product POE
```

For a short-delay MOE pilot, supply **AUX_MOEORB** files and use `--product MOE`.
A POE file cannot be silently substituted into an MOE run or vice versa.
Each run creates a separate, labelled output directory; it never edits
the capture, including its original `pending_poe_validation` manifest.

Process several satellites/captures using a shared reference directory:

```powershell
python -m src.core.prospective_validation --captures-root "data" --reference-dir "data/references" --product MOE
```

The CLI selects standard Sentinel filenames by satellite prefix and product.
It validates the XML mission, product, Earth-fixed frame, UTC time system,
metre / metre-per-second units, state count, and validity coverage.
Incomplete coverage or reference gaps over 20 seconds leave validation pending.
Missing or malformed inputs fail explicitly. No partial horizon is reported.

For a file obtained through another workflow, retain its original Sentinel
filename. Supported format here is Sentinel-1 Earth Explorer XML EOF, not an
arbitrary ephemeris file. Other satellite missions need verified adapters.

## Outputs and method

Each successful validation under `results/prospective_validation/` contains:

- `errors.csv`: exact saved timestamps, forecast age, TLE age, transformed
  predictions, interpolated reference positions, and 3D Euclidean errors.
- `horizons.csv`: first sampled threshold exceedance, crossing bracket,
  linear crossing estimate, and right-censoring flags; defaults 1, 2, 5, 10 km.
- `summary.json`: reference grade, per-capture RMS / maximum errors,
  provenance, hashes, software versions, settings and limitations.
- `error_plot.png`: error against hours from forecast origin.
- `earth_orientation.ecsv`: the IERS table used for the transformation.
- `references/`: copies of the reference files used.

The validator verifies the saved TLE and forecast hashes and checks the
archived time grid. It transforms saved TEME positions into geocentric ITRS
using Astropy at their original timestamps. It uses cubic Hermite
interpolation of Earth-fixed reference positions and velocities to evaluate
the reference at those same times, without extrapolation or rounding.

Astropy may download current Earth orientation data. Data outside its IERS
table are rejected; the result records whether predicted IERS values were used.
Reference EARTH_FIXED coordinates are treated as geocentric ITRS; differences
between terrestrial frame realizations are not modelled. Where reference
files overlap, the latest XML Creation_Date wins at each common timestamp;
all selected files are archived and hashed.

Horizon zero means the initial sampled error already meets the threshold.
A blank estimated horizon plus `right_censored=True` means the threshold
was not reached in the observed window, not that the horizon equals 24 hours.
Crossings between forecast samples can be missed. The linear estimate is
an estimate within its reported sampling bracket, not an exact crossing.

Repeated captures of the same TLE and overlapping forecast windows are
dependent observations. `case_group` identifies the satellite plus input-TLE
hash; it does not prove statistical independence. Do not pool MOE and POE
results or treat all time samples as independent trials. This command provides
per-capture measurements, not a fitted population reliability probability.

The original historical calculations and GUI are unchanged. Recent date
arguments alone do not make a historical reconstruction prospective.

## 3. Historical evaluation on an exact epoch-based grid

For new historical analyses use the following command rather than the legacy
batch CSV/report pipeline:

```powershell
python -m src.core.historical_validation --satellite sentinel1c --tle-file "data/sentinel1c/tle/sentinel1c_sampled_2026-09-01_to_2026-09-01.tle"
```

By default it reads the satellite's existing reference directory and evaluates
every TLE in the explicitly supplied catalog. Use a sampled catalog to retain
the chosen one-TLE-per-day design. Defaults are 24 hours, 60-second spacing and
1, 2, 5 and 10 km thresholds. Both endpoints are included: **1,441 states**.
This is historical reconstruction from the TLE epoch, not evidence that the
TLE was publicly available at that epoch and not a prospective capture.

The command reuses the prospective validator's metadata and full-coverage checks,
TEME-to-ITRS transformation, interpolation and horizon calculation. Each complete
case is published to a new directory under `results/historical_validation/`.
It saves `errors.csv`, `horizons.csv`, `error_plot.png`, `summary.json`,
the reconstructed TEME trajectory, the evaluated TLE, a copy of the source
catalog, copies of reference files and the Earth orientation table used.
Inputs and previous outputs are preserved. An incomplete or failed case
publishes no result directory, and the command exits unsuccessfully if any
case could not be evaluated.

Every elapsed time starts at the TLE epoch. Horizons distinguish the first
sampled exceedance, the bracket between neighboring samples, a linear crossing
estimate and right censoring. Initial exceedance is zero because the initial
epoch is actually evaluated. A censored estimate stays blank rather than
becoming a supposed 24-hour crossing. Fine-scale crossings between samples can
still be missed. The summary labels the evaluation `retrospective` and records
the reference grade, hashes, software versions and processing time.

Optional flags: `--reference-dir`, `--product POE|MOE`, `--duration-hours`,
`--step-seconds`, `--thresholds-km`, `--output-root`, and `--no-plot`.
The supported reference format remains Sentinel-1 Earth Explorer EOF.
This command does not add Sentinel-3 support.

The original `batch_analyzer`, its CSVs and its downstream statistics/report
modules remain legacy tools. They use reference-aligned sampling and an
interpolated horizon relative to the first reference sample, historically
labelled `exact`. That label must not be interpreted as an exact physical
crossing. Do not feed the new historical output files into the legacy report
pipeline or pool measurements with different horizon definitions.

## Checks

```powershell
python -m unittest discover -s tests -v
python -m src.core.prospective_validation --help
```

Tests use synthetic reference trajectories and mocked Space-Track responses,
plus a real Astropy transformation with bundled IERS data. They check timing,
archive integrity, interpolation at fractional seconds, reference metadata,
coverage, censoring, output isolation and product labels. Synthetic tests
do not certify scientific agreement with real POE or MOE. A credential-free
GitHub Actions workflow runs the checks on Windows and Linux with Python 3.13.

## Sources

- [Space-Track documentation](https://www.space-track.org/documentation)
- [Sentinel-1 products and latency](https://sentiwiki.copernicus.eu/web/s1-processing)
- [MOE availability and sampling](https://dataspace.copernicus.eu/news/2025-4-23-availability-new-s1-pod-orbit-file-auxmoeorb-copernicus-data-space-ecosystem)
- [Copernicus POD file specification](https://sentinels.copernicus.eu/documents/d/sentinel/copernicus-pod-service-file-format-specification)
- [Astropy satellite frames](https://docs.astropy.org/en/stable/coordinates/satellites.html)
- [Astropy IERS data](https://docs.astropy.org/en/stable/utils/iers.html)
- [CelesTrak SAR catalog, including Sentinel-1C](https://celestrak.org/Norad/elements/table.php?FORMAT=json&GROUP=sar)
- [Sentinel-1B end of mission](https://www.esa.int/Applications/Observing_the_Earth/Copernicus/Sentinel-1/Mission_ends_for_Copernicus_Sentinel-1B_satellite)
