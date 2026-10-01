"""Deterministic aggregation: per-workout stats, period totals, comparisons and
weekly trends. Pure functions, no I/O. None means "no data" and is never
treated as 0.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import (
    ActivityDetail,
    ActivityRecord,
    ActivityTypeSummary,
    CollectorSummary,
    DetailCollector,
    FieldStats,
    MetricComparison,
    SessionMetrics,
    TrainingPeriodComparison,
    TrainingPeriodSummary,
    TrainingSummary,
    TrainingTrendDelta,
    TrainingTrendSummary,
    TrainingWeekSummary,
    WeekDeltaTransition,
)

HR_TYPE = "com.huawei.instantaneous.exercise_heart_rate"
SPEED_TYPE = "com.huawei.instantaneous.speed"
WEEK_DAYS = 7


# --- Single workout -----------------------------------------------------------


def _summarize_collector(collector: DetailCollector) -> CollectorSummary:
    samples = collector.sample_points
    series: dict[str, list[float | int]] = {}
    for sp in samples:
        for name, value in sp.values.items():
            series.setdefault(name, []).append(value)
    return CollectorSummary(
        data_collector_id=collector.data_collector_id,
        data_type_name=samples[0].data_type_name if samples else None,
        sample_count=len(samples),
        start_time=collector.start_time or (samples[0].timestamp if samples else None),
        end_time=collector.end_time or (samples[-1].timestamp if samples else None),
        fields=[
            FieldStats(
                field_name=name,
                count=len(values),
                min_value=min(values),
                max_value=max(values),
                avg_value=sum(values) / len(values),
                first_value=values[0],
                last_value=values[-1],
            )
            for name, values in series.items()
        ],
    )


def summarize_activity_detail(detail: ActivityDetail) -> TrainingSummary:
    return TrainingSummary(
        activity_id=detail.activity_id,
        activity_type=detail.activity_type,
        start_time=detail.start_time,
        end_time=detail.end_time,
        active_time=detail.active_time,
        time_zone=detail.time_zone,
        collectors=[_summarize_collector(c) for c in detail.details],
    )


def _field_values(detail: ActivityDetail, data_type: str, field: str) -> list[float | int]:
    return [
        sp.values[field]
        for c in detail.details
        for sp in c.sample_points
        if sp.data_type_name == data_type and field in sp.values
    ]


def compute_session_metrics(
    detail: ActivityDetail, record: ActivityRecord | None = None
) -> SessionMetrics:
    """Heart rate and pace prefer the device summary, falling back to samples.
    Distance only comes from the device (integrating samples is inaccurate)."""
    duration = None
    if detail.start_time and detail.end_time:
        duration = (detail.end_time - detail.start_time).total_seconds()

    avg_hr = max_hr = min_hr = hr_source = None
    if record is not None and record.avg_heart_rate is not None:
        avg_hr, max_hr, min_hr = record.avg_heart_rate, record.max_heart_rate, record.min_heart_rate
        hr_source = "device_summary"
    elif hr := _field_values(detail, HR_TYPE, "bpm"):
        avg_hr, max_hr, min_hr = sum(hr) / len(hr), float(max(hr)), float(min(hr))
        hr_source = "samples"

    avg_speed = max_speed = speed_source = None
    if speed := _field_values(detail, SPEED_TYPE, "speed"):
        avg_speed, max_speed = sum(speed) / len(speed), float(max(speed))
        speed_source = "samples"

    avg_pace = best_pace = pace_source = None
    if record is not None and record.avg_pace is not None:
        avg_pace, best_pace = record.avg_pace, record.best_pace
        pace_source = "device_summary"
    elif avg_speed is not None and avg_speed > 0:
        avg_pace = 1000.0 / avg_speed
        best_pace = 1000.0 / max_speed if max_speed and max_speed > 0 else None
        pace_source = "samples"

    distance = record.distance if record is not None else None
    return SessionMetrics(
        activity_id=detail.activity_id,
        duration_seconds=duration,
        active_time_raw=detail.active_time,
        avg_heart_rate=avg_hr,
        max_heart_rate=max_hr,
        min_heart_rate=min_hr,
        heart_rate_source=hr_source,
        avg_speed=avg_speed,
        max_speed=max_speed,
        speed_source=speed_source,
        avg_pace=avg_pace,
        best_pace=best_pace,
        pace_source=pace_source,
        distance=distance,
        distance_source="device_summary" if distance is not None else None,
    )


# --- Period totals ------------------------------------------------------------


def _sum(values: list) -> float | int | None:
    return sum(values) if values else None


def _pace(active_seconds: list[float], km: list[float]) -> float | None:
    """Pace is a ratio: total time / total distance, never a mean of paces."""
    total_km = sum(km)
    return sum(active_seconds) / total_km if active_seconds and total_km > 0 else None


class _Bucket:
    def __init__(self) -> None:
        self.count = 0
        self.distances: list[float] = []
        self.durations: list[float] = []
        self.active_ms: list[int] = []
        self.pace_seconds: list[float] = []
        self.pace_km: list[float] = []

    def add(self, record: ActivityRecord, duration: float | None) -> None:
        self.count += 1
        if record.distance is not None:
            self.distances.append(float(record.distance))
        if duration is not None:
            self.durations.append(duration)
        if record.active_time_ms is not None:
            self.active_ms.append(record.active_time_ms)
            if record.distance is not None:
                self.pace_seconds.append(record.active_time_ms / 1000.0)
                self.pace_km.append(record.distance / 1000.0)

    @property
    def active_seconds(self) -> float | None:
        return sum(self.active_ms) / 1000.0 if self.active_ms else None


def aggregate_activity_period(records: list[ActivityRecord]) -> TrainingPeriodSummary:
    total = _Bucket()
    by_type: dict[int, _Bucket] = {}
    calories, ascents, descents, steps, avg_hrs, max_hrs = [], [], [], [], [], []

    for r in records:
        duration = (
            (r.end_time - r.start_time).total_seconds() if r.start_time and r.end_time else None
        )
        total.add(r, duration)
        if r.activity_type is not None:
            by_type.setdefault(r.activity_type, _Bucket()).add(r, duration)
        for target, value in (
            (calories, r.calories),
            (ascents, r.ascent),
            (descents, r.descent),
            (avg_hrs, r.avg_heart_rate),
            (max_hrs, r.max_heart_rate),
        ):
            if value is not None:
                target.append(float(value))
        if r.steps is not None:
            steps.append(r.steps)

    starts = [r.start_time for r in records if r.start_time]
    ends = [r.end_time for r in records if r.end_time]
    d = total.distances
    return TrainingPeriodSummary(
        activity_count=len(records),
        period_start=min(starts) if starts else None,
        period_end=max(ends) if ends else None,
        total_distance=_sum(d),
        distance_activity_count=len(d),
        total_calories=_sum(calories),
        calories_activity_count=len(calories),
        total_ascent=_sum(ascents),
        total_descent=_sum(descents),
        ascent_activity_count=len(ascents),
        total_steps=_sum(steps),
        steps_activity_count=len(steps),
        total_duration_seconds=sum(total.durations),
        duration_activity_count=len(total.durations),
        total_active_time_seconds=total.active_seconds,
        active_time_activity_count=len(total.active_ms),
        average_pace_seconds_per_km=_pace(total.pace_seconds, total.pace_km),
        pace_activity_count=len(total.pace_seconds),
        average_distance=sum(d) / len(d) if d else None,
        average_duration_seconds=(
            sum(total.durations) / len(total.durations) if total.durations else None
        ),
        max_distance=max(d) if d else None,
        max_duration_seconds=max(total.durations) if total.durations else None,
        average_heart_rate=sum(avg_hrs) / len(avg_hrs) if avg_hrs else None,
        max_heart_rate=max(max_hrs) if max_hrs else None,
        hr_activity_count=len(avg_hrs),
        activity_types=[
            ActivityTypeSummary(
                activity_type=activity_type,
                count=b.count,
                total_distance=_sum(b.distances),
                total_duration_seconds=sum(b.durations),
                total_active_time_seconds=b.active_seconds,
                active_time_activity_count=len(b.active_ms),
                average_pace_seconds_per_km=_pace(b.pace_seconds, b.pace_km),
                pace_activity_count=len(b.pace_seconds),
            )
            for activity_type, b in sorted(by_type.items())
        ],
    )


# --- Comparisons --------------------------------------------------------------

COMPARED_METRICS = (
    "activity_count",
    "total_distance",
    "total_calories",
    "total_ascent",
    "total_descent",
    "total_steps",
    "total_duration_seconds",
    "average_heart_rate",
    "max_heart_rate",
)


def compare_metric(baseline: float | int | None, current: float | int | None) -> MetricComparison:
    if baseline is None or current is None:
        return MetricComparison(baseline=baseline, current=current)
    delta = current - baseline
    return MetricComparison(
        baseline=baseline,
        current=current,
        delta=delta,
        percentage_change=delta / baseline * 100 if baseline != 0 else None,
    )


def _compare_all(
    baseline: TrainingPeriodSummary, current: TrainingPeriodSummary
) -> dict[str, MetricComparison]:
    return {
        m: compare_metric(getattr(baseline, m), getattr(current, m)) for m in COMPARED_METRICS
    }


def compare_training_periods(
    baseline: TrainingPeriodSummary, current: TrainingPeriodSummary
) -> TrainingPeriodComparison:
    return TrainingPeriodComparison(
        baseline_summary=baseline, current_summary=current, **_compare_all(baseline, current)
    )


# --- Weekly trend -------------------------------------------------------------


def build_week_windows(weeks: int, now: datetime) -> list[tuple[datetime, datetime]]:
    """Consecutive rolling 7-day windows [start, end), oldest first, ending at now."""
    if weeks < 1:
        raise ValueError(f"weeks must be >= 1, got {weeks}")
    return [
        (
            now - timedelta(days=(weeks - i) * WEEK_DAYS),
            now - timedelta(days=(weeks - 1 - i) * WEEK_DAYS),
        )
        for i in range(weeks)
    ]


def aggregate_weekly_trend(
    records: list[ActivityRecord], weeks: int = 4, now: datetime | None = None
) -> TrainingTrendSummary:
    """Each workout lands in the window containing its start time."""
    anchor = now or datetime.now(timezone.utc)
    windows = build_week_windows(weeks, anchor)
    buckets: list[list[ActivityRecord]] = [[] for _ in windows]
    for r in records:
        if r.start_time is None:
            continue
        for i, (start, end) in enumerate(windows):
            if start <= r.start_time < end:
                buckets[i].append(r)
                break
    return TrainingTrendSummary(
        anchor=anchor,
        window_days=WEEK_DAYS,
        weeks=[
            TrainingWeekSummary(
                window_start=start, window_end=end, summary=aggregate_activity_period(bucket)
            )
            for (start, end), bucket in zip(windows, buckets)
        ],
    )


def compute_weekly_trend_delta(trend: TrainingTrendSummary) -> TrainingTrendDelta:
    """Week-over-week changes for each adjacent pair (older -> newer)."""
    return TrainingTrendDelta(
        anchor=trend.anchor,
        window_days=trend.window_days,
        transitions=[
            WeekDeltaTransition(
                baseline_window_start=older.window_start,
                baseline_window_end=older.window_end,
                current_window_start=newer.window_start,
                current_window_end=newer.window_end,
                **_compare_all(older.summary, newer.summary),
            )
            for older, newer in zip(trend.weeks, trend.weeks[1:])
        ],
    )
