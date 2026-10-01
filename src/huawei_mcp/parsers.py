"""Turn raw Huawei JSON into models. Never raises on bad input: missing or
malformed values become None and broken entries are skipped.

Timestamp units differ per field and are fixed, not guessed:
- activity list startTime/endTime: ms
- activity detail top-level times: ms; collectors and samples: ns
- sleep record startTime/endTime: ns; go_bed/fall_asleep/wakeup values: ms
- personal best startTime/endTime: ms
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .models import (
    ActivityDetail,
    ActivityRecord,
    AthleticPerformance,
    DailyStat,
    DetailCollector,
    DetailSamplePoint,
    HealthMetricStats,
    PersonalBestEntry,
    SamplePoint,
    SleepRecord,
    SportPersonalBests,
    StatBlock,
)

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# --- Primitive conversions ----------------------------------------------------


def _from_epoch(value: Any, per_second: int) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
        seconds, rem = divmod(n, per_second)
        micros = rem * 1_000_000 // per_second
        return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=micros)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def ms_to_datetime(value: Any) -> datetime | None:
    return _from_epoch(value, 1_000)


def ns_to_datetime(value: Any) -> datetime | None:
    return _from_epoch(value, 1_000_000_000)


def _float(value: Any, allow_str: bool = True) -> float | None:
    """Finite float or None. Numeric strings only accepted when allow_str."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not allow_str:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _strict_int(value: Any) -> int | None:
    """Only real ints (or whole floats); strings and bools are rejected."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _value_of(entry: dict) -> Any:
    for key in ("floatValue", "integerValue", "longValue", "doubleValue", "stringValue"):
        if entry.get(key) is not None:
            return entry[key]
    return None


def _fields_of(item: dict) -> dict[str, Any]:
    """{"value": [{"fieldName": f, "floatValue": v}, ...]} -> {f: v}."""
    values = item.get("value")
    if not isinstance(values, list):
        return {}
    return {
        v["fieldName"]: _value_of(v)
        for v in values
        if isinstance(v, dict) and isinstance(v.get("fieldName"), str)
    }


def _first(fields: dict, *names: str) -> Any:
    for name in names:
        if fields.get(name) is not None:
            return fields[name]
    return None


# --- Activity list ------------------------------------------------------------


def extract_activity_list(raw: Any) -> list[dict]:
    """The list endpoint returns a top-level list; tolerate wrapped dicts too."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("activityRecords", "records", "activityRecordList", "list"):
            if isinstance(raw.get(key), list):
                return raw[key]
    return []


def _summary_map(data_summary: Any) -> dict[str, dict]:
    """dataSummary (dict keyed by type, or list with dataTypeName) -> {type: fields}."""
    if isinstance(data_summary, dict):
        return {k: _fields_of(v) for k, v in data_summary.items() if isinstance(v, dict)}
    if isinstance(data_summary, list):
        return {
            item["dataTypeName"]: _fields_of(item)
            for item in data_summary
            if isinstance(item, dict) and item.get("dataTypeName")
        }
    return {}


def _samples(detail_data: Any, data_type: str) -> list[SamplePoint]:
    samples: list[SamplePoint] = []
    if not isinstance(detail_data, list):
        return samples
    for collector in detail_data:
        if not isinstance(collector, dict):
            continue
        for sp in collector.get("samplePoints") or []:
            if not isinstance(sp, dict) or sp.get("dataTypeName") != data_type:
                continue
            time = ns_to_datetime(sp.get("startTime"))
            values = list(_fields_of(sp).values())
            value = _float(values[0]) if values else None
            if time is not None and value is not None:
                samples.append(SamplePoint(time=time, value=value))
    return sorted(samples, key=lambda s: s.time)


def _positive(value: float | None) -> float | None:
    return value if value is not None and value > 0 else None


def parse_activity_record(raw: dict) -> ActivityRecord:
    """Summaries sit under activitySummary; top-level placement is a fallback."""
    activity_summary = raw.get("activitySummary")
    if not isinstance(activity_summary, dict):
        activity_summary = {}
    summary = _summary_map(activity_summary.get("dataSummary") or raw.get("dataSummary"))
    perf = activity_summary.get("performanceSummary") or raw.get("performanceSummary")
    if not isinstance(perf, dict):
        perf = {}
    pace = activity_summary.get("paceSummary")
    if not isinstance(pace, dict):
        pace = {}

    hr = summary.get("com.huawei.continuous.heart_rate.statistics", {})
    speed = summary.get("com.huawei.continuous.speed.statistics", {})
    altitude = summary.get("com.huawei.continuous.altitude.statistics", {})
    avg_speed = _positive(_float(_first(speed, "averageSpeed", "avgSpeed", "average", "avg")))
    max_speed = _positive(_float(_first(speed, "maxSpeed", "max")))

    # Device pace (s/km) wins; <= 0 means "no value", fall back to 1000 / speed.
    avg_pace = _positive(_float(pace.get("avgPace"))) or (1000.0 / avg_speed if avg_speed else None)
    best_pace = _positive(_float(pace.get("bestPace"))) or (1000.0 / max_speed if max_speed else None)
    active_time = _int(raw.get("activeTime"))

    return ActivityRecord(
        activity_id=raw.get("activityId") or raw.get("id"),
        start_time=ms_to_datetime(raw.get("startTime")),
        end_time=ms_to_datetime(raw.get("endTime")),
        activity_type=raw.get("activityType") or raw.get("type"),
        active_time=active_time,
        active_time_ms=active_time,
        distance=_float(
            _first(
                summary.get("com.huawei.continuous.distance.total", {}),
                "distance", "totalDistance", "value", "sum",
            )
        ),
        calories=_float(
            _first(
                summary.get("com.huawei.continuous.calories.burnt.total", {}),
                "calories_total", "caloriesTotal", "totalCalories",
            )
        ),
        ascent=_float(_first(altitude, "ascent_total", "ascentTotal")),
        descent=_float(_first(altitude, "descent_total", "descentTotal")),
        steps=_int(
            _first(
                summary.get("com.huawei.continuous.steps.total", {}),
                "steps", "stepCount", "totalSteps",
            )
        ),
        avg_heart_rate=_float(_first(hr, "averageHeartRate", "avgHeartRate", "average", "avg", "mean")),
        max_heart_rate=_float(_first(hr, "maxHeartRate", "max")),
        min_heart_rate=_float(_first(hr, "minHeartRate", "min")),
        avg_pace=avg_pace,
        best_pace=best_pace,
        vo2_max=_float(perf.get("vo2Max")),
        recovery_time=_int(perf.get("recoveryTime")),
        training_load=_float(perf.get("aerobicTrainingStress")),
        heart_rate_samples=_samples(
            raw.get("detailData"), "com.huawei.instantaneous.exercise_heart_rate"
        ),
        cadence_samples=_samples(raw.get("detailData"), "com.huawei.instantaneous.steps.rate"),
    )


# --- Activity detail ----------------------------------------------------------


def _sample_values(value: Any) -> dict[str, float | int]:
    """Each entry must have a fieldName and exactly one of floatValue / integerValue."""
    values: dict[str, float | int] = {}
    if not isinstance(value, list):
        return values
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("fieldName")
        has_float, has_int = "floatValue" in item, "integerValue" in item
        if not isinstance(name, str) or not name or has_float == has_int:
            continue
        try:
            values[name] = float(item["floatValue"]) if has_float else int(item["integerValue"])
        except (TypeError, ValueError, OverflowError):
            continue
    return values


def _parse_collector(collector: Any) -> DetailCollector:
    if not isinstance(collector, dict):
        return DetailCollector()
    points = []
    for sp in collector.get("samplePoints") or []:
        if not isinstance(sp, dict):
            continue
        timestamp = ns_to_datetime(sp.get("startTime"))
        name = sp.get("dataTypeName")
        if timestamp is None or not isinstance(name, str) or not name:
            continue
        points.append(
            DetailSamplePoint(
                timestamp=timestamp, data_type_name=name, values=_sample_values(sp.get("value"))
            )
        )
    return DetailCollector(
        data_collector_id=collector.get("dataCollectorId"),
        start_time=ns_to_datetime(collector.get("startTime")),
        end_time=ns_to_datetime(collector.get("endTime")),
        sample_points=points,
    )


def parse_activity_detail(raw: Any) -> ActivityDetail:
    """The detail endpoint returns a list holding one detail object."""
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    if not isinstance(raw, dict):
        return ActivityDetail()
    time_zone = raw.get("timeZone")
    return ActivityDetail(
        activity_id=raw.get("id") or raw.get("activityId"),
        activity_type=_int(raw.get("activityType")),
        start_time=ms_to_datetime(raw.get("startTime")),
        end_time=ms_to_datetime(raw.get("endTime")),
        active_time=_int(raw.get("activeTime")),
        time_zone=time_zone if isinstance(time_zone, str) else None,
        details=[_parse_collector(c) for c in raw.get("details") or []],
    )


# --- Sleep and recovery stats -------------------------------------------------

_SLEEP_TIME_FIELDS = {
    "go_bed_time": "go_bed_time",
    "fall_asleep_time": "fall_asleep_time",
    "wakeup_time": "wakeup_time",
}
_SLEEP_INT_FIELDS = {
    "light_sleep_time": "light_sleep_minutes",
    "deep_sleep_time": "deep_sleep_minutes",
    "dream_time": "dream_sleep_minutes",
    "awake_time": "awake_minutes",
    "all_sleep_time": "total_sleep_minutes",
    "sleep_latency": "sleep_latency_minutes",
    "wakeup_count": "wakeup_count",
    "deep_sleep_part": "deep_sleep_part",
    "sleep_score": "sleep_score",
    "sleep_efficiency": "sleep_efficiency_percent",
    "sleep_type": "sleep_type",
}


def parse_sleep_records(raw: Any) -> list[SleepRecord]:
    """Sorted by start time; fragment sub-data is ignored."""
    items = raw.get("healthRecords") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    records = []
    for item in items:
        if not isinstance(item, dict):
            continue
        values = _fields_of(item)
        record_id = item.get("id")
        fields: dict[str, Any] = {
            "record_id": record_id if isinstance(record_id, str) else None,
            "start_time": ns_to_datetime(item.get("startTime")),
            "end_time": ns_to_datetime(item.get("endTime")),
        }
        for source, target in _SLEEP_TIME_FIELDS.items():
            fields[target] = ms_to_datetime(_strict_int(values.get(source)))
        for source, target in _SLEEP_INT_FIELDS.items():
            fields[target] = _strict_int(values.get(source))
        records.append(SleepRecord(**fields))
    return sorted(records, key=lambda r: r.start_time or EPOCH)


def _stat_block(statistics: Any) -> StatBlock:
    if not isinstance(statistics, dict):
        return StatBlock()
    return StatBlock(
        avg=_float(statistics.get("avg"), allow_str=False),
        max=_float(statistics.get("max"), allow_str=False),
        min=_float(statistics.get("min"), allow_str=False),
        count=_strict_int(statistics.get("count")),
    )


def _matching(entries: Any, field_name: str | None) -> list[dict]:
    if not isinstance(entries, list):
        return []
    dicts = [e for e in entries if isinstance(e, dict)]
    return dicts if field_name is None else [e for e in dicts if e.get("fieldName") == field_name]


def _day_str(day: Any) -> str | None:
    """YYYYMMDD (int or str) -> YYYY-MM-DD."""
    if isinstance(day, bool) or not isinstance(day, (int, str)):
        return None
    text = str(day)
    if len(text) != 8 or not (text.isascii() and text.isdigit()):
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def parse_health_metric_stats(
    raw: Any,
    *,
    data_type: str,
    days: int,
    start_day: str,
    end_day: str,
    field_name: str | None = None,
) -> HealthMetricStats:
    """overall from `results`, daily from `groupResults` (sorted, one per day).

    field_name=None lets the server pick it (resting HR comes back as restBpm).
    """
    overall = StatBlock()
    daily: list[DailyStat] = []
    if isinstance(raw, dict):
        results = _matching(raw.get("results"), field_name)
        if results:
            server_field = results[0].get("fieldName")
            if isinstance(server_field, str) and server_field:
                field_name = server_field
            overall = _stat_block(results[0].get("statistics"))

        pending: list[tuple[str, dict]] = []
        groups = raw.get("groupResults")
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                continue
            for entry in _matching(group.get("groupStatistics"), field_name):
                day = _day_str(entry.get("startDay"))
                if day is not None:
                    pending.append((day, entry))
        seen: set[str] = set()
        for day, entry in sorted(pending, key=lambda p: p[0]):
            if day not in seen:
                seen.add(day)
                daily.append(DailyStat(day=day, stats=_stat_block(entry.get("statistics"))))

    return HealthMetricStats(
        data_type=data_type,
        field_name=field_name,
        days=days,
        start_day=start_day,
        end_day=end_day,
        overall=overall,
        daily=daily,
    )


# --- Performance --------------------------------------------------------------


def parse_athletic_performance(raw: Any) -> AthleticPerformance:
    if not isinstance(raw, dict):
        return AthleticPerformance()
    predicted = raw.get("predictedTimes")
    times = {}
    if isinstance(predicted, dict):
        for key, value in predicted.items():
            number = _float(value, allow_str=False)
            if number is not None:
                times[str(key)] = number
    return AthleticPerformance(
        running_ability=_float(raw.get("runningAbility"), allow_str=False),
        condition=_float(raw.get("condition"), allow_str=False),
        fitness=_float(raw.get("fitness"), allow_str=False),
        fatigue=_float(raw.get("fatigue"), allow_str=False),
        ranking=_float(raw.get("ranking"), allow_str=False),
        predicted_times=times,
    )


def parse_personal_bests(raw: Any, activity_type: str) -> SportPersonalBests:
    """Uses the first sportReports entry whose activityType matches."""
    bests = SportPersonalBests(activity_type=activity_type)
    reports = raw.get("sportReports") if isinstance(raw, dict) else None
    if not isinstance(reports, list):
        return bests
    report = next(
        (r for r in reports if isinstance(r, dict) and r.get("activityType") == activity_type),
        None,
    )
    entries = report.get("personalBest") if report else None
    if not isinstance(entries, list):
        return bests
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        bests.personal_bests.append(
            PersonalBestEntry(
                name=name,
                value=_float(entry.get("value"), allow_str=False),
                start_time=ms_to_datetime(_strict_int(entry.get("startTime"))),
                end_time=ms_to_datetime(_strict_int(entry.get("endTime"))),
            )
        )
    return bests
