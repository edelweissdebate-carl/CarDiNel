"""Anonymous ESA Swarm SP3xCOM_2_ download and strict reference reading.

GPS calendar epochs are converted through TAI (GPS = TAI - 19 s), using
Astropy's leap-second table. SP3 positions are km, velocities are dm/s.
Only reduced-dynamic centre-of-mass products in the official ZIP container
are accepted. No arbitrary SP3/GNSS files or medium-precision MOD products.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from pathlib import Path
from time import sleep
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from src.core.satellites import SatelliteConfig, get_satellite
from src.core.prospective_validation import ReferenceUnavailable, State, digest
from src.io.eof_parser import element_text, parse_utc

ARCHIVE_URL = "https://swarm-diss.eo.esa.int/"
PATTERN = re.compile(
    r"^SW_OPER_SP3(?P<letter>[ABC])COM_2__(?P<start>\d{8}T\d{6})_"
    r"(?P<stop>\d{8}T\d{6})_(?P<version>\d{4})\.ZIP$", re.IGNORECASE
)
MAX_ZIP_BYTES = 10_000_000
MAX_UNCOMPRESSED_BYTES = 20_000_000
EARTH_FIXED_FRAMES = {"ITRF", "ITR08", "ITR14", "ITR20", "IGS08", "IGB08",
                      "IGS14", "IGB14", "IGS20", "IGB20", "IGC20"}


def _archive_session():
    """Retry transient failures when reading the public archive."""
    session = requests.Session()
    retry = Retry(total=3, connect=3, read=3, status=3, backoff_factor=1,
                  allowed_methods=frozenset({"GET"}),
                  status_forcelist=(429, 500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


@dataclass(frozen=True)
class SwarmProduct:
    filename: str
    letter: str
    start: datetime
    stop: datetime
    baseline: int

    @property
    def archive_path(self) -> str:
        return f"swarm/Level2daily/Latest_baselines/POD/RD/Sat_{self.letter}/{self.filename}"

    @property
    def download_url(self) -> str:
        return ARCHIVE_URL + "?" + urlencode({"do": "download", "file": self.archive_path})


def parse_filename(filename: str) -> SwarmProduct:
    match = PATTERN.fullmatch(filename)
    if match is None:
        raise ValueError("Expected an official Swarm SP3xCOM_2_ ZIP filename.")
    start, stop = [
        datetime.strptime(match.group(key), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        for key in ("start", "stop")
    ]
    if stop <= start:
        raise ValueError("Swarm product validity must have positive duration.")
    return SwarmProduct(filename, match.group("letter").upper(), start, stop, int(match.group("version")))


def swarm_letter(config: SatelliteConfig) -> str:
    if config.reference_provider != "swarm_sp3" or config.key not in {"swarma", "swarmb", "swarmc"}:
        raise ValueError("Expected a configured Swarm satellite.")
    return config.key[-1].upper()


def _calendar(fields: list[str]) -> str:
    year, month, day, hour, minute = [int(value) for value in fields[:5]]
    seconds = float(fields[5])
    if not math.isfinite(seconds) or not 0 <= seconds < 60:
        raise ValueError("Invalid GPS calendar second.")
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{seconds:011.8f}"


def parse_sp3(text: str, expected_id: str) -> tuple[list[State], dict]:
    """Read fixed-width SP3c/d position/velocity records without rounding epochs."""
    from astropy.time import Time, TimeDelta

    lines = text.splitlines()
    if not lines or lines[0][:3] not in {"#cV", "#dV"}:
        raise ValueError("Expected SP3c/d with explicit velocity records.")
    first = lines[0]
    count = int(first[32:39])
    frame = first[46:51].strip()
    if count <= 0 or frame.upper() not in EARTH_FIXED_FRAMES:
        raise ValueError("Unsupported or missing Earth-fixed SP3 frame or epoch count.")
    time_headers = [line for line in lines if line.startswith("%c")]
    if not time_headers or time_headers[0][9:12] != "GPS":
        raise ValueError("Swarm precise references must declare GPS time.")
    satellite_lines = [line for line in lines if line.startswith("+") and not line.startswith("++")]
    identifiers = []
    for line in satellite_lines:
        identifiers.extend(line[index:index + 3].strip() for index in range(9, len(line), 3)
                           if line[index:index + 3].strip() not in {"", "0"})
    if (not satellite_lines or int(satellite_lines[0][3:6]) != 1
            or identifiers != [expected_id]):
        raise ValueError("SP3 satellite identifier does not match the selected spacecraft.")
    interval_headers = [line for line in lines if line.startswith("##")]
    if len(interval_headers) != 1:
        raise ValueError("Missing SP3 interval header.")
    nominal_interval = float(interval_headers[0][24:38])
    if not math.isfinite(nominal_interval) or nominal_interval <= 0:
        raise ValueError("Invalid SP3 sampling interval.")
    calendars, positions, velocities = [], [], []
    position = velocity = None
    current = None
    ended = False

    def finish():
        if current is not None:
            if position is None or velocity is None:
                raise ValueError("SP3 epoch has an incomplete position/velocity pair.")
            calendars.append(current)
            positions.append(position)
            velocities.append(velocity)

    for line in lines[1:]:
        if line.startswith("*"):
            finish()
            current = _calendar(line[1:].split())
            position = velocity = None
        elif line.startswith(("P", "V")):
            if current is None or ended or line[1:4] != expected_id:
                raise ValueError("Unexpected SP3 state identity or ordering.")
            values = tuple(float(line[4 + 14 * i:18 + 14 * i]) for i in range(3))
            if not all(math.isfinite(value) for value in values):
                raise ValueError("Non-finite SP3 coordinates.")
            if line.startswith("P"):
                if position is not None or values == (0.0, 0.0, 0.0) or line[79:80] == "P":
                    raise ValueError("Missing, duplicate or predicted SP3 position.")
                position = values
            else:
                if velocity is not None:
                    raise ValueError("Duplicate SP3 velocity.")
                velocity = tuple(value * 0.0001 for value in values)
        elif line.strip() == "EOF":
            finish()
            current = None
            ended = True
        elif current is not None and line.strip() and not line.startswith(("EP", "EV")):
            raise ValueError("Unsupported record inside SP3 trajectory.")
    if not ended or len(calendars) != count:
        raise ValueError("Truncated SP3 file or epoch count mismatch.")
    tai = Time(calendars, format="isot", scale="tai") + TimeDelta(19, format="sec")
    times = list(tai.utc.to_datetime(timezone=timezone.utc))
    header_time = _calendar([first[3:7], first[8:10], first[11:13],
                             first[14:16], first[17:19], first[20:31]])
    if calendars[0] != header_time:
        raise ValueError("SP3 first epoch disagrees with its header.")
    if any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError("SP3 timestamps must be strictly increasing.")
    # A large interval is left for the full-coverage checker to reject;
    # smaller or irregular intervals contradict this restricted product.
    if any(abs((right - left).total_seconds() / nominal_interval
               - round((right - left).total_seconds() / nominal_interval)) > 1e-6
           for left, right in zip(times, times[1:])):
        raise ValueError("SP3 states do not follow the declared sampling grid.")
    states = [State(stamp, p, v) for stamp, p, v in zip(times, positions, velocities)]
    return states, {"native_frame": frame, "native_time_system": "GPS",
                    "position_unit": "km", "native_velocity_unit": "dm/s",
                    "velocity_to_km_s_factor": 0.0001,
                    "nominal_interval_seconds": nominal_interval,
                    "gps_to_utc_method": "Astropy via TAI = GPS + 19 s; UTC leap-second conversion"}


def read_swarm_product(path: Path, config: SatelliteConfig) -> tuple[list[State], dict]:
    path = Path(path)
    info = parse_filename(path.name)
    if info.letter != swarm_letter(config):
        raise ValueError("Swarm filename belongs to another spacecraft.")
    before_hash = digest(path)
    if path.stat().st_size > MAX_ZIP_BYTES:
        raise ValueError("Swarm ZIP is unexpectedly large.")
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if (len(members) != 2 or len({member.filename for member in members}) != 2
                or any(member.flag_bits & 1 or member.is_dir() for member in members)
                or sum(member.file_size for member in members) > MAX_UNCOMPRESSED_BYTES):
            raise ValueError("Expected a small, unencrypted SP3 plus HDR ZIP.")
        stem = path.stem
        expected = {stem + ".sp3", stem + ".HDR"}
        if {member.filename for member in members} != expected:
            raise ValueError("Swarm archive members do not match its product filename.")
        header = ET.fromstring(archive.read(stem + ".HDR"))
        sp3_bytes = archive.read(stem + ".sp3")  # zipfile verifies each member CRC.
    fixed = next((node for node in header if node.tag.split("}")[-1] == "Fixed_Header"), None)
    if fixed is None:
        raise ValueError("Missing Swarm Fixed_Header.")
    if (element_text(fixed, "Mission") != "Swarm"
            or element_text(fixed, "File_Name") != path.stem
            or element_text(fixed, "File_Type") != f"SP3{info.letter}COM_2_"
            or int(element_text(fixed, "File_Version")) != info.baseline):
        raise ValueError("Swarm product header identity or version mismatch.")
    if (int(element_text(header, "Product_Err")) != 0
            or element_text(header, "Quality_Indicator") != "000"):
        raise ValueError("Swarm product reports a quality or processing error.")
    start = parse_utc(element_text(fixed, "Validity_Start"))
    stop = parse_utc(element_text(fixed, "Validity_Stop"))
    creation = parse_utc(element_text(fixed, "Creation_Date"))
    if start != info.start or stop != info.stop:
        raise ValueError("Swarm validity header does not match its filename.")
    states, metadata = parse_sp3(sp3_bytes.decode("ascii"), config.reference_satellite_id)
    if (states[0].time != start or states[-1].time >= stop
            or any(not start <= state.time < stop for state in states)):
        raise ValueError("Swarm SP3 states disagree with declared UTC validity.")
    if digest(path) != before_hash:
        raise ValueError("Swarm reference changed while it was read.")
    return states, {
        "filename": path.name, "source_path": str(path.resolve()), "sha256": before_hash,
        "source_url": info.download_url, "provider": "ESA Swarm / TU Delft",
        "product": f"SP3{info.letter}COM_2_", "reference_grade": "PSO",
        "sp3_satellite_id": config.reference_satellite_id, "baseline": info.baseline,
        "creation_utc": creation.isoformat(), "validity_start_utc": start.isoformat(),
        "validity_stop_utc": stop.isoformat(), "actual_last_state_utc": states[-1].time.isoformat(),
        "sp3_member_sha256": hashlib.sha256(sp3_bytes).hexdigest(), **metadata,
    }


def read_swarm_references(
    paths: list[Path], config: SatelliteConfig, origin: datetime, end: datetime,
    *, max_gap_seconds: float = 20,
) -> tuple[list[State], list[dict]]:
    if not math.isfinite(max_gap_seconds) or max_gap_seconds <= 0:
        raise ValueError("Maximum reference gap must be finite and positive.")
    candidates = []
    margin = timedelta(seconds=max_gap_seconds)
    for path in paths:
        info = parse_filename(Path(path).name)
        if info.letter != swarm_letter(config):
            raise ValueError("Wrong Swarm satellite in the supplied reference files.")
        if info.stop < origin - margin or info.start > end + margin:
            continue
        states, provenance = read_swarm_product(Path(path), config)
        candidates.append((info.baseline, provenance["creation_utc"], str(path), states, provenance))
    by_time, provenance = {}, []
    for _, _, _, states, item in sorted(candidates, key=lambda row: row[:3]):
        for state in states:
            by_time[state.time] = state
        provenance.append(item)
    states = sorted(by_time.values(), key=lambda state: state.time)
    if not states or states[0].time > origin or states[-1].time < end:
        raise ReferenceUnavailable("Swarm references do not cover both evaluation endpoints.")
    for left, right in zip(states, states[1:]):
        if right.time >= origin and left.time <= end and (right.time - left.time).total_seconds() > max_gap_seconds:
            raise ReferenceUnavailable("Swarm references have a gap across the evaluation window.")
    return states, provenance


def discover_swarm_products(
    satellite_key: str, start: datetime, stop: datetime, *, session=None, page_size: int = 1000,
) -> list[SwarmProduct]:
    config = get_satellite(satellite_key)
    letter = swarm_letter(config)
    if (start.tzinfo is None or start.utcoffset() is None or stop.tzinfo is None
            or stop.utcoffset() is None or stop < start):
        raise ValueError("Provide ordered timezone-aware discovery boundaries.")
    if not isinstance(page_size, int) or isinstance(page_size, bool) or page_size <= 0:
        raise ValueError("Page size must be positive.")
    session = session or _archive_session()
    products = {}
    directory = f"swarm/Level2daily/Latest_baselines/POD/RD/Sat_{letter}"
    seen_pages = set()
    # Include neighboring daily files for interpolation at UTC/GPS day boundaries.
    margin = timedelta(seconds=20)
    for page in range(100):
        response = session.get(ARCHIVE_URL, params={
            "do": "list", "file": directory, "maxfiles": page_size, "pos": page * page_size,
        }, timeout=(30, 90))
        response.raise_for_status()
        payload = response.json()
        if payload.get("success") is not True or not isinstance(payload.get("results"), list):
            raise ValueError("ESA Swarm archive returned an invalid listing.")
        entries = payload["results"]
        names = tuple(entry.get("name", "") for entry in entries)
        if names and names in seen_pages:
            raise ValueError("Swarm archive repeated a page; refusing incomplete discovery.")
        seen_pages.add(names)
        for entry in entries:
            if entry.get("is_dir") or not entry.get("is_readable"):
                continue
            try:
                product = parse_filename(entry["name"])
            except ValueError:
                continue
            if product.letter != letter or entry.get("path") != product.archive_path:
                raise ValueError("Unexpected path or satellite in the ESA archive listing.")
            if product.start <= stop + margin and product.stop >= start - margin:
                # Keep only the highest processing baseline for a daily validity period.
                key = (product.start, product.stop)
                if key not in products or product.baseline > products[key].baseline:
                    products[key] = product
        if len(entries) < page_size:
            return sorted(products.values(), key=lambda info: info.start)
    raise RuntimeError("ESA Swarm listing exceeded the discovery page limit.")


def _download_zip(session, product: SwarmProduct, staging: Path):
    # Adapter retries cover connections/headers; streamed body failures need a
    # fresh request and a truncated staging file. Never publish a partial ZIP.
    for attempt in range(3):
        response = None
        try:
            response = session.get(ARCHIVE_URL, params={"do": "download", "file": product.archive_path},
                                   timeout=(30, 90), stream=True)
            response.raise_for_status()
            size = 0
            with staging.open("wb") as stream:
                for block in response.iter_content(chunk_size=65536):
                    size += len(block)
                    if size > MAX_ZIP_BYTES:
                        raise ValueError("Swarm download exceeds the expected size limit.")
                    stream.write(block)
            return
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
            if attempt == 2:
                raise
            print(f"Temporary transfer failure; restarting {product.filename} ({attempt + 2}/3)...")
            sleep(2 ** attempt)
        finally:
            if response is not None:
                response.close()


def download_swarm_orbits(
    satellite_key: str, start_date: date, end_date: date, output_folder: Path, *, session=None,
) -> list[Path]:
    if end_date < start_date:
        raise ValueError("End date cannot precede start date.")
    config = get_satellite(satellite_key)
    swarm_letter(config)
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    stop = datetime.combine(end_date, time.max, tzinfo=timezone.utc)
    session = session or _archive_session()
    print(f"Searching ESA's public precise-orbit archive for {config.name}...")
    products = discover_swarm_products(config.key, start, stop, session=session)
    if not products:
        raise ReferenceUnavailable("No Swarm precise science orbits found for the requested dates.")
    destination = Path(output_folder)
    destination.mkdir(parents=True, exist_ok=True)
    paths, provenance = [], []
    for number, product in enumerate(products, 1):
        target = destination / product.filename
        if target.exists():
            _, item = read_swarm_product(target, config)
            print(f"[{number}/{len(products)}] Reusing validated ZIP: {target.name}")
        else:
            print(f"[{number}/{len(products)}] Downloading: {target.name}")
            with tempfile.TemporaryDirectory(dir=destination, prefix=".partial-") as temporary:
                staging = Path(temporary) / product.filename
                _download_zip(session, product, staging)
                _, item = read_swarm_product(staging, config)
                os.replace(staging, target)
            item["source_path"] = str(target.resolve())
        paths.append(target)
        provenance.append(item)
    metadata = {
        "provider": "ESA Swarm", "satellite_key": config.key, "reference_grade": "PSO",
        "requested_start_utc": start.isoformat(), "requested_stop_utc": stop.isoformat(),
        "download_checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": provenance,
    }
    # A uniquely named record preserves provenance from earlier downloads.
    record = destination / ("download_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    record.write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Reference files ready: {len(paths)}\nSaved in:\n{destination}")
    return paths
