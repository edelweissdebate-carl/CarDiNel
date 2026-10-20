from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote

import requests


BASE_URL = "https://www.space-track.org"
LOGIN_URL = f"{BASE_URL}/ajaxauth/login"


class SpaceTrackError(RuntimeError):
    """Raised when a Space-Track request fails."""


@dataclass(frozen=True)
class TLEDownloadRequest:
    norad_catalog_id: int
    start_date: date
    end_date: date
    output_file: Path

    def validate(self) -> None:
        if self.norad_catalog_id <= 0:
            raise ValueError("NORAD catalog ID must be positive.")

        if self.end_date < self.start_date:
            raise ValueError(
                "The end date cannot be earlier than the start date."
            )


class SpaceTrackClient:
    def __init__(
        self,
        username: str,
        password: str,
        timeout_seconds: int = 60,
    ) -> None:
        if not username.strip():
            raise ValueError("Space-Track username is missing.")

        if not password:
            raise ValueError("Space-Track password is missing.")

        self.username = username.strip()
        self.password = password
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

        self.session.headers.update(
            {
                "User-Agent": (
                    "ORBIT-TRUST/0.2 "
                    "(student computational astrodynamics research)"
                )
            }
        )

    def login(self) -> None:
        response = self.session.post(
            LOGIN_URL,
            data={
                "identity": self.username,
                "password": self.password,
            },
            timeout=self.timeout_seconds,
        )

        if response.status_code != 200:
            raise SpaceTrackError(
                "Space-Track login request failed with HTTP "
                f"{response.status_code}."
            )

        response_text = response.text.lower()

        failure_phrases = (
            "failed login",
            "invalid username",
            "invalid password",
            "authentication failed",
        )

        if any(
            phrase in response_text
            for phrase in failure_phrases
        ):
            raise SpaceTrackError(
                "Space-Track rejected the username or password."
            )

        # A successful login normally creates session cookies.
        if not self.session.cookies:
            raise SpaceTrackError(
                "Space-Track did not create a login session. "
                "Check your credentials and internet connection."
            )

    @staticmethod
    def build_history_query(
        norad_catalog_id: int,
        start_date: date,
        end_date: date,
    ) -> str:
        """
        Build a GP_History query returning legacy two-line TLE records.

        Example:
        /class/gp_history/NORAD_CAT_ID/39634/
        EPOCH/2024-01-01--2024-01-03/
        orderby/EPOCH ASC/format/tle
        """
        date_range = (
            f"{start_date.isoformat()}--{end_date.isoformat()}"
        )

        order_value = quote("EPOCH ASC", safe="")

        return (
            f"{BASE_URL}/basicspacedata/query"
            f"/class/gp_history"
            f"/NORAD_CAT_ID/{norad_catalog_id}"
            f"/EPOCH/{date_range}"
            f"/orderby/{order_value}"
            f"/format/tle"
        )

    def download_historical_tles(
        self,
        request: TLEDownloadRequest,
    ) -> int:
        request.validate()

        query_url = self.build_history_query(
            norad_catalog_id=request.norad_catalog_id,
            start_date=request.start_date,
            end_date=request.end_date,
        )

        response = self.session.get(
            query_url,
            timeout=self.timeout_seconds,
        )

        if response.status_code != 200:
            raise SpaceTrackError(
                "Historical TLE request failed with HTTP "
                f"{response.status_code}."
            )

        tle_text = response.text.strip()

        if not tle_text:
            raise SpaceTrackError(
                "Space-Track returned no TLEs for the selected "
                "satellite and date range."
            )

        lowered_text = tle_text.lower()

        if "<html" in lowered_text:
            raise SpaceTrackError(
                "Space-Track returned an HTML page instead of TLE "
                "data. Your login session may have expired."
            )

        lines = [
            line.rstrip()
            for line in tle_text.splitlines()
            if line.strip()
        ]

        line_1_count = sum(
            line.startswith("1 ")
            for line in lines
        )

        line_2_count = sum(
            line.startswith("2 ")
            for line in lines
        )

        if line_1_count == 0 or line_1_count != line_2_count:
            raise SpaceTrackError(
                "The downloaded response does not contain complete "
                "TLE pairs."
            )

        request.output_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        request.output_file.write_text(
            tle_text + "\n",
            encoding="utf-8",
        )

        return line_1_count

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "SpaceTrackClient":
        return self

    def __exit__(
        self,
        exception_type,
        exception,
        traceback,
    ) -> None:
        self.close()