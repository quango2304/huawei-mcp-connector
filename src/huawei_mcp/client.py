"""HTTP client for the Huawei Health "healthrunninggroup" API.

Returns raw JSON only; parsing lives in parsers.py. Credentials are passed in
per instance and never stored.
"""

from __future__ import annotations

from typing import Any

import httpx

# Data lives in one region per account. The token is accepted everywhere,
# but only the home region returns data.
REGION_HOSTS = {
    "dra": "hihealthbase-dra.things.dbankcloud.com",  # Asia-Pacific
    "dre": "hihealthbase-dre.things.dbankcloud.com",  # Europe
    "drcn": "hihealthbase-drcn.things.dbankcloud.cn",  # China
}
API_PATH = "/healthrunninggroup/v1"

# Client id of the health.cloud.huawei.com web app (same for every user).
DEFAULT_CLIENT_ID = "106533743"

SLEEP_RECORD_DATA_TYPE = "com.huawei.health.record.sleep"
SLEEP_FRAGMENT_SUB_DATA_TYPE = "com.huawei.continuous.sleep.fragment"
RESTING_HR_DATA_TYPE = "com.huawei.instantaneous.resting_heart_rate"

# periodStatistics requests are rejected (400) without strategy/groupOption/timeZone.
STATS_STRATEGY = ["MAX", "MIN", "AVG", "SUM"]
GROUP_OPTION_DAY = "day"


class HuaweiError(Exception):
    """Base error for API calls (network, HTTP, bad JSON)."""


class HuaweiHTTPError(HuaweiError):
    def __init__(self, status_code: int, url: str, body: str = ""):
        self.status_code = status_code
        self.url = url
        super().__init__(
            f"Huawei API returned HTTP {status_code} for {url}"
            + (f": {body}" if body else "")
        )


class HuaweiClient:
    """Thin wrapper over httpx; one instance per tool call."""

    def __init__(
        self,
        authorization: str,
        client_id: str = DEFAULT_CLIENT_ID,
        region: str = "dra",
        timeout: float = 30.0,
    ):
        if region not in REGION_HOSTS:
            raise ValueError(
                f"Unknown region {region!r}; use one of {', '.join(REGION_HOSTS)}"
            )
        self.base_url = f"https://{REGION_HOSTS[region]}{API_PATH}"
        self._http = httpx.Client(
            headers={"Authorization": authorization, "x-client-id": client_id},
            timeout=timeout,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = self._http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise HuaweiError(f"Network error calling {url}: {exc}") from exc
        if not response.is_success:
            raise HuaweiHTTPError(response.status_code, url, (response.text or "")[:200])
        try:
            return response.json()
        except ValueError as exc:
            raise HuaweiError(f"Invalid JSON from {url}: {exc}") from exc

    def query_activity_records(
        self, start_ms: str, end_ms: str, limit: int = 5, order: str = "endTimeDesc"
    ) -> Any:
        """Workout list. Times are millisecond timestamps as strings."""
        return self._request(
            "POST",
            "/activityRecord:query",
            json={"startTime": start_ms, "endTime": end_ms, "limit": limit, "order": order},
        )

    def query_activity_detail(
        self, start_ms: str, end_ms: str, activity_type: str, data_types: list[str]
    ) -> Any:
        """High-frequency samples for one workout (same endpoint, different body)."""
        return self._request(
            "POST",
            "/activityRecord:query",
            json={
                "startTime": start_ms,
                "endTime": end_ms,
                "activityType": activity_type,
                "detailDataType": data_types,
                "highFreqDetailsPreferred": True,
            },
        )

    def query_sleep_records(self, start_ns: int, end_ns: int) -> Any:
        """Sleep records. Times are nanosecond timestamps."""
        return self._request(
            "GET",
            "/healthRecords",
            params={
                "startTime": start_ns,
                "endTime": end_ns,
                "dataType": SLEEP_RECORD_DATA_TYPE,
                "subDataType": SLEEP_FRAGMENT_SUB_DATA_TYPE,
            },
        )

    @staticmethod
    def _stats_body(
        data_type: str,
        start_day: str | int,
        end_day: str | int,
        tz_offset: str,
        field_names: list[str] | None = None,
    ) -> dict[str, Any]:
        req: dict[str, Any] = {"dataType": data_type}
        if field_names is not None:
            req["fieldNames"] = field_names
        req |= {
            "startDay": start_day,
            "endDay": end_day,
            "strategy": STATS_STRATEGY,
            "groupOption": GROUP_OPTION_DAY,
            "timeZone": tz_offset,
        }
        return {"reqs": [req], "groupReqs": [dict(req)]}

    def query_health_record_stats(
        self, field_names: list[str], start_day: str, end_day: str, tz_offset: str
    ) -> Any:
        """Daily stats over sleep records. Days are "YYYYMMDD" strings."""
        return self._request(
            "POST",
            "/healthRecords/periodStatistics:calculate",
            json=self._stats_body(
                SLEEP_RECORD_DATA_TYPE, start_day, end_day, tz_offset, field_names
            ),
        )

    def query_sample_set_stats(self, start_day: int, end_day: int, tz_offset: str) -> Any:
        """Daily resting heart-rate stats. Days are YYYYMMDD integers."""
        return self._request(
            "POST",
            "/sampleSet/periodStatistics:calculate",
            json=self._stats_body(RESTING_HR_DATA_TYPE, start_day, end_day, tz_offset),
        )

    def query_athletic_performance(self, tz_offset: str) -> Any:
        return self._request(
            "GET", "/athleticPerformance/latest", params={"timeZone": tz_offset}
        )

    def query_sport_reports(self, activity_type: str = "running") -> Any:
        return self._request(
            "GET", "/sportReports", params={"activityType": activity_type}
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> HuaweiClient:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
