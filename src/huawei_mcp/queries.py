"""Fetch + parse + aggregate, one function per tool. No MCP dependency."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

from .analysis import (
    aggregate_activity_period,
    aggregate_weekly_trend,
    compare_training_periods,
    compute_weekly_trend_delta,
)
from .client import RESTING_HR_DATA_TYPE, SLEEP_RECORD_DATA_TYPE, HuaweiClient
from .models import (
    ActivityDetail,
    ActivityRecord,
    AthleticPerformance,
    HealthMetricStats,
    SleepRecord,
    SportPersonalBests,
    TrainingPeriodComparison,
    TrainingPeriodSummary,
    TrainingTrendDelta,
    TrainingTrendSummary,
)
from .parsers import (
    extract_activity_list,
    parse_activity_detail,
    parse_activity_record,
    parse_athletic_performance,
    parse_health_metric_stats,
    parse_personal_bests,
    parse_sleep_records,
)

MAX_DAYS = 730
MAX_LIMIT = 100
MAX_WINDOW_DAYS = MAX_DAYS // 2
MAX_WEEKS = MAX_DAYS // 7
DETAIL_LOOKUP_LIMIT = 50
HRV_FIELD = "avgHrv"

DETAIL_DATA_TYPES = [
    "com.huawei.instantaneous.exercise_heart_rate",
    "com.huawei.recovery_heart_rate",
    "com.huawei.instantaneous.speed",
    "com.huawei.instantaneous.steps.rate",
    "com.huawei.continuous.run.posture",
    "com.huawei.instantaneous.altitude",
    "com.huawei.instantaneous.location.sample",
    "com.huawei.analog_power",
]


def check_range(name: str, value: int, maximum: int) -> int:
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}, got {value}")
    return value


def _ms(dt: datetime) -> str:
    return str(int(dt.timestamp() * 1000))


def _records(client: HuaweiClient, start: datetime, end: datetime, limit: int) -> list[ActivityRecord]:
    raw = client.query_activity_records(_ms(start), _ms(end), limit=limit)
    return [parse_activity_record(item) for item in extract_activity_list(raw)]


# --- Workouts -----------------------------------------------------------------


def recent_activities(client: HuaweiClient, limit: int, days: int) -> list[ActivityRecord]:
    check_range("limit", limit, MAX_LIMIT)
    check_range("lookback_days", days, MAX_DAYS)
    now = datetime.now(timezone.utc)
    return _records(client, now - timedelta(days=days), now, limit)


def session_data(
    client: HuaweiClient, activity_id: str, days: int
) -> tuple[ActivityRecord, ActivityDetail]:
    """The detail API can't look up by id: find the workout in the list first,
    then query samples with its exact start/end/type."""
    if not activity_id:
        raise ValueError("activity_id is required")
    check_range("lookback_days", days, MAX_DAYS)
    now = datetime.now(timezone.utc)
    raw_list = client.query_activity_records(
        _ms(now - timedelta(days=days)), _ms(now), limit=DETAIL_LOOKUP_LIMIT
    )
    record = next(
        (
            r
            for r in extract_activity_list(raw_list)
            if isinstance(r, dict) and (r.get("activityId") or r.get("id")) == activity_id
        ),
        None,
    )
    if record is None:
        raise ValueError(
            f"No workout {activity_id} in the last {days} days. "
            "Check get_recent_activities or raise lookback_days."
        )
    start, end, activity_type = record.get("startTime"), record.get("endTime"), record.get("activityType")
    if start is None or end is None or activity_type is None:
        raise ValueError(f"Workout {activity_id} has no startTime/endTime/activityType")
    raw_detail = client.query_activity_detail(
        str(start), str(end), str(activity_type), DETAIL_DATA_TYPES
    )
    return parse_activity_record(record), parse_activity_detail(raw_detail)


def period_summary(client: HuaweiClient, days: int, limit: int) -> TrainingPeriodSummary:
    return aggregate_activity_period(recent_activities(client, limit, days))


def period_comparison(client: HuaweiClient, window_days: int, limit: int) -> TrainingPeriodComparison:
    """Baseline [now-2N, now-N) vs current [now-N, now). Records are filtered by
    their own start time so neither window leaks into the other."""
    check_range("window_days", window_days, MAX_WINDOW_DAYS)
    check_range("limit", limit, MAX_LIMIT)
    now = datetime.now(timezone.utc)
    split = now - timedelta(days=window_days)
    summaries = []
    for start, end in ((split - timedelta(days=window_days), split), (split, now)):
        records = [
            r for r in _records(client, start, end, limit)
            if r.start_time is not None and start <= r.start_time < end
        ]
        summaries.append(aggregate_activity_period(records))
    return compare_training_periods(*summaries)


def weekly_trend(client: HuaweiClient, weeks: int, limit: int) -> TrainingTrendSummary:
    """One list request for the whole span, then split into weeks locally."""
    check_range("weeks", weeks, MAX_WEEKS)
    check_range("limit", limit, MAX_LIMIT)
    now = datetime.now(timezone.utc)
    records = _records(client, now - timedelta(days=weeks * 7), now, limit)
    return aggregate_weekly_trend(records, weeks=weeks, now=now)


def weekly_trend_delta(client: HuaweiClient, weeks: int, limit: int) -> TrainingTrendDelta:
    return compute_weekly_trend_delta(weekly_trend(client, weeks, limit))


# --- Recovery -----------------------------------------------------------------


def _tz_offset(tz: tzinfo) -> str:
    return datetime.now(tz).strftime("%z")  # e.g. +0700


def _day_window(days: int, tz: tzinfo) -> tuple[str, str]:
    """Last N calendar days including today, as YYYYMMDD (both ends inclusive)."""
    today = datetime.now(tz).date()
    start = today - timedelta(days=days - 1)
    return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")


def _iso_day(ymd: str) -> str:
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"


def sleep_records(client: HuaweiClient, days: int) -> list[SleepRecord]:
    check_range("days", days, MAX_DAYS)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    raw = client.query_sleep_records(
        int(start.timestamp()) * 1_000_000_000, int(now.timestamp()) * 1_000_000_000
    )
    return parse_sleep_records(raw)


def resting_heart_rate(client: HuaweiClient, days: int, tz: tzinfo) -> HealthMetricStats:
    check_range("days", days, MAX_DAYS)
    start, end = _day_window(days, tz)
    raw = client.query_sample_set_stats(int(start), int(end), _tz_offset(tz))
    return parse_health_metric_stats(
        raw, data_type=RESTING_HR_DATA_TYPE, days=days,
        start_day=_iso_day(start), end_day=_iso_day(end),
    )


def hrv_stats(client: HuaweiClient, days: int, tz: tzinfo) -> HealthMetricStats:
    check_range("days", days, MAX_DAYS)
    start, end = _day_window(days, tz)
    raw = client.query_health_record_stats([HRV_FIELD], start, end, _tz_offset(tz))
    return parse_health_metric_stats(
        raw, data_type=SLEEP_RECORD_DATA_TYPE, days=days,
        start_day=_iso_day(start), end_day=_iso_day(end), field_name=HRV_FIELD,
    )


# --- Performance --------------------------------------------------------------


def athletic_performance(client: HuaweiClient, tz: tzinfo) -> AthleticPerformance:
    return parse_athletic_performance(client.query_athletic_performance(_tz_offset(tz)))


def personal_bests(client: HuaweiClient, activity_type: str) -> SportPersonalBests:
    activity_type = (activity_type or "").strip()
    if not activity_type:
        raise ValueError("activity_type must be a non-empty string, e.g. 'running'")
    raw = client.query_sport_reports(activity_type)
    return parse_personal_bests(raw, activity_type)
