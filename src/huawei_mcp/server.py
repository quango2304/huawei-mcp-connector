"""MCP server. Each request carries the caller's own Huawei credentials in
HTTP headers, so one instance can serve many users and stores nothing.

Headers:
  Authorization     required  "Bearer <token>" copied from the Huawei Health web app
  x-huawei-region   optional  dra | dre | drcn   (default: $HUAWEI_DEFAULT_REGION or dra)
  x-timezone        optional  IANA name, e.g. Asia/Ho_Chi_Minh   (default: $TZ or UTC)
  x-client-id       optional  defaults to the web app's id
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers

from . import queries
from .analysis import compute_session_metrics, summarize_activity_detail
from .client import DEFAULT_CLIENT_ID, REGION_HOSTS, HuaweiClient, HuaweiError, HuaweiHTTPError

DEFAULT_REGION = os.environ.get("HUAWEI_DEFAULT_REGION", "dra")

mcp = FastMCP(
    "Huawei Health",
    instructions=(
        "Read-only access to the caller's Huawei Health data: workouts, sleep, "
        "resting heart rate, HRV, running ability and personal bests."
    ),
)


def _timezone(name: str | None) -> tzinfo:
    name = name or os.environ.get("TZ") or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ToolError(f"Unknown x-timezone {name!r}; use an IANA name like Asia/Ho_Chi_Minh") from exc


@contextmanager
def _session() -> Iterator[tuple[HuaweiClient, tzinfo]]:
    """Build a client from the request headers and turn API errors into
    readable tool errors."""
    headers = get_http_headers(include={"authorization"})
    token = headers.get("authorization", "").strip()
    if not token:
        raise ToolError(
            "Missing Authorization header. Add your Huawei Health token to the MCP "
            "client config (see README)."
        )
    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"
    region = headers.get("x-huawei-region", DEFAULT_REGION).strip().lower()
    if region not in REGION_HOSTS:
        raise ToolError(f"Unknown x-huawei-region {region!r}; use one of {', '.join(REGION_HOSTS)}")
    tz = _timezone(headers.get("x-timezone"))
    client = HuaweiClient(token, headers.get("x-client-id", DEFAULT_CLIENT_ID), region)
    try:
        yield client, tz
    except HuaweiHTTPError as exc:
        if exc.status_code == 401:
            raise ToolError(
                "Huawei rejected the token (401). Tokens expire after about an hour; "
                "copy a fresh Authorization header from health.cloud.huawei.com."
            ) from exc
        raise ToolError(str(exc)) from exc
    except HuaweiError as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    finally:
        client.close()


@mcp.tool()
def health_check() -> dict:
    """Check that the server is up. Does not call Huawei."""
    return {"status": "ok", "service": "huawei-mcp-connector"}


@mcp.tool()
def get_recent_activities(limit: int = 5, lookback_days: int = 30) -> list[dict]:
    """List recent workouts, newest first.

    Args:
        limit: max workouts to return (1-100).
        lookback_days: how far back to search (1-730). Raise it for older workouts.

    Each workout has activity_id, start/end time, activity_type (raw Huawei code),
    distance (m), calories, ascent/descent, steps, heart rate, pace (s/km),
    vo2_max, recovery_time (h) and training_load. Missing values are null.
    """
    with _session() as (client, _):
        records = queries.recent_activities(client, limit, lookback_days)
    return [r.model_dump(mode="json") for r in records]


@mcp.tool()
def get_activity_detail(activity_id: str, lookback_days: int = 30) -> dict:
    """Raw high-frequency samples for one workout (heart rate, speed, cadence,
    altitude, GPS, running posture, power).

    Args:
        activity_id: from get_recent_activities.
        lookback_days: window used to find the workout (1-730).

    details[] holds one entry per sensor stream; each sample has a timestamp,
    data_type_name and values such as {"bpm": 142.0}. Some streams may be empty.
    Large output: prefer get_training_summary unless raw samples are needed.
    """
    with _session() as (client, _):
        _, detail = queries.session_data(client, activity_id, lookback_days)
    return detail.model_dump(mode="json")


@mcp.tool()
def get_training_summary(activity_id: str, lookback_days: int = 30) -> dict:
    """Per-stream statistics for one workout: sample count, time range and
    min / max / avg / first / last of every field (e.g. bpm, speed).

    Args:
        activity_id: from get_recent_activities.
        lookback_days: window used to find the workout (1-730).
    """
    with _session() as (client, _):
        _, detail = queries.session_data(client, activity_id, lookback_days)
    return summarize_activity_detail(detail).model_dump(mode="json")


@mcp.tool()
def get_session_metrics(activity_id: str, lookback_days: int = 30) -> dict:
    """Headline metrics for one workout: duration, heart rate, speed (m/s),
    pace (s/km) and distance (m). Each group has a *_source of
    "device_summary" (watch's own numbers) or "samples" (computed here).

    Args:
        activity_id: from get_recent_activities.
        lookback_days: window used to find the workout (1-730).
    """
    with _session() as (client, _):
        record, detail = queries.session_data(client, activity_id, lookback_days)
    return compute_session_metrics(detail, record).model_dump(mode="json")


@mcp.tool()
def get_training_period_summary(lookback_days: int = 7, limit: int = 100) -> dict:
    """Totals across all workouts in the last N days: count, distance, duration,
    active time, average pace, calories, ascent/descent, steps, heart rate, and
    the same per activity_type.

    Args:
        lookback_days: period length (1-730).
        limit: max workouts included (1-100).

    duration includes pauses; total_active_time_seconds does not. Each
    *_activity_count tells how many workouts had that metric.
    """
    with _session() as (client, _):
        summary = queries.period_summary(client, lookback_days, limit)
    return summary.model_dump(mode="json")


@mcp.tool()
def get_training_period_comparison(window_days: int = 7, limit: int = 100) -> dict:
    """Compare the last N days with the N days before (e.g. 7 = this week vs last).

    Args:
        window_days: length of each window (1-365).
        limit: max workouts per window (1-100).

    Returns both period summaries plus baseline / current / delta /
    percentage_change for each total and heart-rate metric. delta is null when
    either side has no data; percentage_change is null when the baseline is 0.
    """
    with _session() as (client, _):
        comparison = queries.period_comparison(client, window_days, limit)
    now = datetime.now(timezone.utc)
    split = now - timedelta(days=window_days)
    result = comparison.model_dump(mode="json")
    result["window_days"] = window_days
    result["baseline_window"] = {
        "start": (split - timedelta(days=window_days)).isoformat(),
        "end": split.isoformat(),
    }
    result["current_window"] = {"start": split.isoformat(), "end": now.isoformat()}
    return result


@mcp.tool()
def get_training_weekly_trend(weeks: int = 4, limit: int = 100) -> dict:
    """Training volume per week for the last N weeks (rolling 7-day windows
    ending now, oldest first), each with a full period summary.

    Args:
        weeks: number of weeks (1-104).
        limit: max workouts over the whole span (1-100); older ones are cut off.
    """
    with _session() as (client, _):
        trend = queries.weekly_trend(client, weeks, limit)
    return trend.model_dump(mode="json")


@mcp.tool()
def get_training_weekly_trend_delta(weeks: int = 4, limit: int = 100) -> dict:
    """Week-over-week changes for the last N weeks (same windows as
    get_training_weekly_trend). Returns weeks-1 transitions, oldest first, each
    with baseline / current / delta / percentage_change per metric.

    Args:
        weeks: number of weeks (1-104).
        limit: max workouts over the whole span (1-100).
    """
    with _session() as (client, _):
        delta = queries.weekly_trend_delta(client, weeks, limit)
    return delta.model_dump(mode="json")


@mcp.tool()
def get_sleep_records(days: int = 14) -> list[dict]:
    """Nightly sleep for the last N days, oldest first.

    Args:
        days: 1-730.

    Each night: bed / fall-asleep / wake-up times, light / deep / REM (dream) /
    awake / total minutes, sleep score, efficiency (%), latency (min) and
    wake-up count. Nights without data are omitted.
    """
    with _session() as (client, _):
        records = queries.sleep_records(client, days)
    return [r.model_dump(mode="json") for r in records]


@mcp.tool()
def get_resting_heart_rate(days: int = 28) -> dict:
    """Resting heart rate (bpm) for the last N calendar days including today:
    overall avg / max / min plus one entry per day with data.

    Args:
        days: 1-730.
    """
    with _session() as (client, tz):
        stats = queries.resting_heart_rate(client, days, tz)
    return stats.model_dump(mode="json")


@mcp.tool()
def get_hrv_stats(days: int = 28) -> dict:
    """Nightly heart-rate variability (avgHrv) for the last N calendar days
    including today: overall avg / max / min plus one entry per day with data.
    Values are passed through as Huawei reports them (typically ms).

    Args:
        days: 1-730.
    """
    with _session() as (client, tz):
        stats = queries.hrv_stats(client, days, tz)
    return stats.model_dump(mode="json")


@mcp.tool()
def get_athletic_performance() -> dict:
    """Latest running-ability assessment from Huawei: running_ability, condition
    (positive = fresh, negative = fatigued), fitness, fatigue, ranking, and
    predicted race times in seconds (km1, km3, km5, km10, halfMarathon, marathon)."""
    with _session() as (client, tz):
        performance = queries.athletic_performance(client, tz)
    return performance.model_dump(mode="json")


@mcp.tool()
def get_personal_bests(activity_type: str = "running") -> dict:
    """Personal records for one sport, e.g. bestRunDistance (m) or
    bestRunPartTime10KM (s), with when they were set.

    Args:
        activity_type: sport name; only "running" is verified.
    """
    with _session() as (client, _):
        bests = queries.personal_bests(client, activity_type)
    return bests.model_dump(mode="json")


def main() -> None:
    mcp.run(
        transport="http",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        path="/mcp",
        stateless_http=True,
        show_banner=False,
    )


if __name__ == "__main__":
    main()
