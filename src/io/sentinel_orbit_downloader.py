from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore import UNSIGNED
from botocore.client import Config


BUCKET_NAME = "s1-orbits"
POEORB_PREFIX = "AUX_POEORB/"

ORBIT_FILENAME_PATTERN = re.compile(
    r"^(?P<satellite>S1[ABCD])_OPER_AUX_POEORB_OPOD_"
    r"(?P<creation>\d{8}T\d{6})_"
    r"V(?P<start>\d{8}T\d{6})_"
    r"(?P<stop>\d{8}T\d{6})\.EOF$"
)


class SentinelOrbitDownloadError(RuntimeError):
    """Raised when precise-orbit discovery or download fails."""


@dataclass(frozen=True)
class OrbitFileInfo:
    key: str
    filename: str
    satellite: str
    validity_start: datetime
    validity_stop: datetime


def parse_compact_time(value: str) -> datetime:
    return datetime.strptime(
        value,
        "%Y%m%dT%H%M%S",
    ).replace(tzinfo=timezone.utc)


def parse_orbit_filename(key: str) -> OrbitFileInfo | None:
    filename = Path(key).name
    match = ORBIT_FILENAME_PATTERN.match(filename)

    if match is None:
        return None

    return OrbitFileInfo(
        key=key,
        filename=filename,
        satellite=match.group("satellite"),
        validity_start=parse_compact_time(match.group("start")),
        validity_stop=parse_compact_time(match.group("stop")),
    )


def intervals_overlap(
    first_start: datetime,
    first_stop: datetime,
    second_start: datetime,
    second_stop: datetime,
) -> bool:
    return first_start <= second_stop and second_start <= first_stop


class SentinelOrbitDownloader:
    def __init__(self) -> None:
        self.s3 = boto3.client(
            "s3",
            config=Config(signature_version=UNSIGNED),
            region_name="us-west-2",
        )

    def list_matching_files(
        self,
        satellite: str,
        start_time: datetime,
        stop_time: datetime,
    ) -> list[OrbitFileInfo]:
        satellite = satellite.upper().strip()

        if satellite not in {"S1A", "S1B", "S1C", "S1D"}:
            raise ValueError(
                "Satellite must be S1A, S1B, S1C, or S1D."
            )

        if start_time.tzinfo is None or stop_time.tzinfo is None:
            raise ValueError(
                "Start and stop times must be timezone-aware."
            )

        if stop_time < start_time:
            raise ValueError(
                "Stop time cannot be earlier than start time."
            )

        paginator = self.s3.get_paginator("list_objects_v2")
        matching: list[OrbitFileInfo] = []

        try:
            pages = paginator.paginate(
                Bucket=BUCKET_NAME,
                Prefix=POEORB_PREFIX,
            )

            for page in pages:
                for item in page.get("Contents", []):
                    info = parse_orbit_filename(item["Key"])

                    if info is None:
                        continue

                    if info.satellite != satellite:
                        continue

                    if intervals_overlap(
                        info.validity_start,
                        info.validity_stop,
                        start_time,
                        stop_time,
                    ):
                        matching.append(info)

        except Exception as error:
            raise SentinelOrbitDownloadError(
                f"Could not list Sentinel orbit files: {error}"
            ) from error

        matching.sort(
            key=lambda file_info: file_info.validity_start
        )

        return matching

    def download_files(
        self,
        files: list[OrbitFileInfo],
        output_folder: str | Path,
    ) -> list[Path]:
        destination = Path(output_folder)
        destination.mkdir(parents=True, exist_ok=True)

        downloaded_paths: list[Path] = []

        for number, file_info in enumerate(files, start=1):
            output_path = destination / file_info.filename

            if output_path.exists():
                print(
                    f"[{number}/{len(files)}] Already exists: "
                    f"{file_info.filename}"
                )
                downloaded_paths.append(output_path)
                continue

            print(
                f"[{number}/{len(files)}] Downloading: "
                f"{file_info.filename}"
            )

            try:
                self.s3.download_file(
                    BUCKET_NAME,
                    file_info.key,
                    str(output_path),
                )
            except Exception as error:
                raise SentinelOrbitDownloadError(
                    f"Failed to download {file_info.filename}: {error}"
                ) from error

            downloaded_paths.append(output_path)

        return downloaded_paths