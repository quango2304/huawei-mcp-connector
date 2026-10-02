"""MCP server (local, single-user).

Credentials come from the environment. The server keeps a trusted browser profile
and refreshes the Huawei access token by itself; the only manual step is entering an
SMS code the first time (or whenever Huawei drops the trust), via start_login /
submit_sms_code. Data requests use httpx with the current token.
"""

from __future__ import annotations

import atexit
import os
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from . import queries
from .analysis import compute_session_metrics, summarize_activity_detail
from .auth import CaptchaRequired, CodeRequired, LoginError, LoginManager
from .client import HuaweiClient, HuaweiError, HuaweiHTTPError

PROFILE_DIR = os.environ.get("HUAWEI_PROFILE_DIR", "/data/profile")

mcp = FastMCP(
    "Huawei Health",
    instructions=(
        "Read-only access to the owner's Huawei Health data: workouts, sleep, "
        "resting heart rate, HRV, running ability and personal bests. If a tool "
        "reports that login is required, call start_login, ask the user for the SMS "
        "code it triggers, then call submit_sms_code."
    ),
)

_manager: LoginManager | None = None


def _get_manager() -> LoginManager:
    global _manager
    if _manager is None:
        account = os.environ.get("HUAWEI_ACCOUNT", "")
        password = os.environ.get("HUAWEI_PASSWORD", "")
        if not account or not password:
            raise ToolError("HUAWEI_ACCOUNT and HUAWEI_PASSWORD are not set in the server environment")
        _manager = LoginManager(account, password, PROFILE_DIR, headless=True)
    return _manager


def _timezone() -> tzinfo:
    name = os.environ.get("HUAWEI_TZ") or os.environ.get("TZ") or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _call(fn: Callable[[HuaweiClient, tzinfo], object]) -> object:
    """Run fn with a ready client, refreshing the token and mapping errors.

    Retries once if the token is rejected (401) after a forced refresh.
    """
    manager = _get_manager()
    tz = _timezone()
    for attempt in (1, 2):
        try:
            token = manager.get_token()
        except CodeRequired:
            raise ToolError("Login required. Call start_login, then submit_sms_code with the SMS code.")
        except CaptchaRequired as e:
            raise ToolError(str(e))
        except LoginError as e:
            raise ToolError(f"Login failed: {e}")
        client = HuaweiClient(f"Bearer {token.value}", region=token.region)
        try:
            return fn(client, tz)
        except HuaweiHTTPError as e:
            if e.status_code == 401 and attempt == 1:
                manager.invalidate()
                continue
            raise ToolError(str(e))
        except HuaweiError as e:
            raise ToolError(str(e))
        except ValueError as e:
            raise ToolError(str(e))
        finally:
            client.close()
    raise ToolError("Could not obtain a valid token")


# --- Auth tools -------------------------------------------------------------


@mcp.tool()
def login_status() -> dict:
    """Whether the server currently holds a valid Huawei session."""
    return _get_manager().status()


@mcp.tool()
def start_login() -> dict:
    """Begin login with the configured account. On a trusted device this logs in
    silently; otherwise it sends an SMS and returns status "sms_sent" — then call
    submit_sms_code. May return "captcha_required" if Huawei demands a captcha."""
    return _get_manager().start_login()


@mcp.tool()
def submit_sms_code(code: str) -> dict:
    """Finish a login started with start_login by entering the SMS code. Also marks
    this browser as trusted, so later logins refresh without a code."""
    try:
        return _get_manager().submit_sms_code(code)
    except LoginError as e:
        raise ToolError(str(e))


# --- Data tools -------------------------------------------------------------


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
    recs = _call(lambda c, _tz: queries.recent_activities(c, limit, lookback_days))
    return [r.model_dump(mode="json") for r in recs]


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
    detail = _call(lambda c, _tz: queries.session_data(c, activity_id, lookback_days)[1])
    return detail.model_dump(mode="json")


@mcp.tool()
def get_training_summary(activity_id: str, lookback_days: int = 30) -> dict:
    """Per-stream statistics for one workout: sample count, time range and
    min / max / avg / first / last of every field (e.g. bpm, speed).

    Args:
        activity_id: from get_recent_activities.
        lookback_days: window used to find the workout (1-730).
    """
    detail = _call(lambda c, _tz: queries.session_data(c, activity_id, lookback_days)[1])
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
    def run(c, _tz):
        record, detail = queries.session_data(c, activity_id, lookback_days)
        return compute_session_metrics(detail, record)
    return _call(run).model_dump(mode="json")


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
    return _call(lambda c, _tz: queries.period_summary(c, lookback_days, limit)).model_dump(mode="json")


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
    comparison = _call(lambda c, _tz: queries.period_comparison(c, window_days, limit))
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
    return _call(lambda c, _tz: queries.weekly_trend(c, weeks, limit)).model_dump(mode="json")


@mcp.tool()
def get_training_weekly_trend_delta(weeks: int = 4, limit: int = 100) -> dict:
    """Week-over-week changes for the last N weeks (same windows as
    get_training_weekly_trend). Returns weeks-1 transitions, oldest first, each
    with baseline / current / delta / percentage_change per metric.

    Args:
        weeks: number of weeks (1-104).
        limit: max workouts over the whole span (1-100).
    """
    return _call(lambda c, _tz: queries.weekly_trend_delta(c, weeks, limit)).model_dump(mode="json")


@mcp.tool()
def get_sleep_records(days: int = 14) -> list[dict]:
    """Nightly sleep for the last N days, oldest first.

    Args:
        days: 1-730.

    Each night: bed / fall-asleep / wake-up times, light / deep / REM (dream) /
    awake / total minutes, sleep score, efficiency (%), latency (min) and
    wake-up count. Nights without data are omitted.
    """
    recs = _call(lambda c, _tz: queries.sleep_records(c, days))
    return [r.model_dump(mode="json") for r in recs]


@mcp.tool()
def get_resting_heart_rate(days: int = 28) -> dict:
    """Resting heart rate (bpm) for the last N calendar days including today:
    overall avg / max / min plus one entry per day with data.

    Args:
        days: 1-730.
    """
    return _call(lambda c, tz: queries.resting_heart_rate(c, days, tz)).model_dump(mode="json")


@mcp.tool()
def get_hrv_stats(days: int = 28) -> dict:
    """Nightly heart-rate variability (avgHrv) for the last N calendar days
    including today: overall avg / max / min plus one entry per day with data.
    Values are passed through as Huawei reports them (typically ms).

    Args:
        days: 1-730.
    """
    return _call(lambda c, tz: queries.hrv_stats(c, days, tz)).model_dump(mode="json")


@mcp.tool()
def get_athletic_performance() -> dict:
    """Latest running-ability assessment from Huawei: running_ability, condition
    (positive = fresh, negative = fatigued), fitness, fatigue, ranking, and
    predicted race times in seconds (km1, km3, km5, km10, halfMarathon, marathon)."""
    return _call(lambda c, tz: queries.athletic_performance(c, tz)).model_dump(mode="json")


@mcp.tool()
def get_personal_bests(activity_type: str = "running") -> dict:
    """Personal records for one sport, e.g. bestRunDistance (m) or
    bestRunPartTime10KM (s), with when they were set.

    Args:
        activity_type: sport name; only "running" is verified.
    """
    return _call(lambda c, _tz: queries.personal_bests(c, activity_type)).model_dump(mode="json")


@atexit.register
def _shutdown() -> None:
    if _manager is not None:
        _manager.close()


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
