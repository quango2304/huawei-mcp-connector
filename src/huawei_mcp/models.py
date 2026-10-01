"""Normalized data models returned by the MCP tools.

Units: distance m, duration s, active_time ms, heart rate bpm, pace s/km,
speed m/s, recovery_time h, sleep segments min. Missing data is None, never 0.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# --- Workouts ---------------------------------------------------------------


class SamplePoint(BaseModel):
    time: datetime
    value: float


class ActivityRecord(BaseModel):
    """One workout from the activity list (device summary values)."""

    activity_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    activity_type: int | None = None  # raw Huawei code, e.g. 56 / 90
    active_time: int | None = None  # ms
    active_time_ms: int | None = None  # ms, same value as active_time
    distance: float | None = None
    calories: float | None = None  # kcal
    ascent: float | None = None
    descent: float | None = None
    steps: int | None = None

    avg_heart_rate: float | None = None
    max_heart_rate: float | None = None
    min_heart_rate: float | None = None

    avg_pace: float | None = None
    best_pace: float | None = None
    pace_map: list[SamplePoint] = Field(default_factory=list)

    vo2_max: float | None = None
    recovery_time: int | None = None
    training_load: float | None = None  # aerobicTrainingStress

    heart_rate_samples: list[SamplePoint] = Field(default_factory=list)
    cadence_samples: list[SamplePoint] = Field(default_factory=list)


class DetailSamplePoint(BaseModel):
    """One sample; values maps fieldName -> number (e.g. {"bpm": 142.0})."""

    timestamp: datetime
    data_type_name: str
    values: dict[str, float | int] = Field(default_factory=dict)


class DetailCollector(BaseModel):
    data_collector_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    sample_points: list[DetailSamplePoint] = Field(default_factory=list)


class ActivityDetail(BaseModel):
    """High-frequency samples for one workout. Requested types may be absent."""

    activity_id: str | None = None
    activity_type: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    active_time: int | None = None  # ms
    time_zone: str | None = None
    details: list[DetailCollector] = Field(default_factory=list)


class FieldStats(BaseModel):
    field_name: str
    count: int
    min_value: float | int
    max_value: float | int
    avg_value: float
    first_value: float | int | None = None
    last_value: float | int | None = None


class CollectorSummary(BaseModel):
    data_collector_id: str | None = None
    data_type_name: str | None = None
    sample_count: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None
    fields: list[FieldStats] = Field(default_factory=list)


class TrainingSummary(BaseModel):
    activity_id: str | None = None
    activity_type: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    active_time: int | None = None  # ms
    time_zone: str | None = None
    collectors: list[CollectorSummary] = Field(default_factory=list)


class SessionMetrics(BaseModel):
    """Per-workout metrics. *_source is "device_summary", "samples" or None."""

    activity_id: str | None = None
    duration_seconds: float | None = None
    active_time_raw: int | None = None  # ms

    avg_heart_rate: float | None = None
    max_heart_rate: float | None = None
    min_heart_rate: float | None = None
    heart_rate_source: str | None = None

    avg_speed: float | None = None
    max_speed: float | None = None
    speed_source: str | None = None

    avg_pace: float | None = None
    best_pace: float | None = None
    pace_source: str | None = None

    distance: float | None = None
    distance_source: str | None = None


# --- Periods and trends -----------------------------------------------------


class ActivityTypeSummary(BaseModel):
    activity_type: int
    count: int = 0
    total_distance: float | None = None
    total_duration_seconds: float = 0.0
    total_active_time_seconds: float | None = None
    active_time_activity_count: int = 0
    average_pace_seconds_per_km: float | None = None
    pace_activity_count: int = 0


class TrainingPeriodSummary(BaseModel):
    """Totals over many workouts.

    duration = end - start (includes pauses); active time excludes pauses.
    Average pace = sum(active s) / sum(km) over workouts having both values.
    Each *_activity_count says how many workouts contributed to that metric.
    """

    activity_count: int = 0
    period_start: datetime | None = None
    period_end: datetime | None = None

    total_distance: float | None = None
    distance_activity_count: int = 0
    total_calories: float | None = None
    calories_activity_count: int = 0
    total_ascent: float | None = None
    total_descent: float | None = None
    ascent_activity_count: int = 0
    total_steps: int | None = None
    steps_activity_count: int = 0
    total_duration_seconds: float = 0.0
    duration_activity_count: int = 0
    total_active_time_seconds: float | None = None
    active_time_activity_count: int = 0
    average_pace_seconds_per_km: float | None = None
    pace_activity_count: int = 0

    average_distance: float | None = None
    average_duration_seconds: float | None = None
    max_distance: float | None = None
    max_duration_seconds: float | None = None

    average_heart_rate: float | None = None
    max_heart_rate: float | None = None
    hr_activity_count: int = 0

    activity_types: list[ActivityTypeSummary] = Field(default_factory=list)


class MetricComparison(BaseModel):
    """delta = current - baseline (None if either side is None).
    percentage_change is None when baseline is None or 0."""

    baseline: float | int | None = None
    current: float | int | None = None
    delta: float | int | None = None
    percentage_change: float | None = None


class TrainingPeriodComparison(BaseModel):
    baseline_summary: TrainingPeriodSummary
    current_summary: TrainingPeriodSummary

    activity_count: MetricComparison = Field(default_factory=MetricComparison)
    total_distance: MetricComparison = Field(default_factory=MetricComparison)
    total_calories: MetricComparison = Field(default_factory=MetricComparison)
    total_ascent: MetricComparison = Field(default_factory=MetricComparison)
    total_descent: MetricComparison = Field(default_factory=MetricComparison)
    total_steps: MetricComparison = Field(default_factory=MetricComparison)
    total_duration_seconds: MetricComparison = Field(default_factory=MetricComparison)
    average_heart_rate: MetricComparison = Field(default_factory=MetricComparison)
    max_heart_rate: MetricComparison = Field(default_factory=MetricComparison)


class TrainingWeekSummary(BaseModel):
    """One rolling 7-day window [window_start, window_end)."""

    window_start: datetime
    window_end: datetime
    summary: TrainingPeriodSummary


class TrainingTrendSummary(BaseModel):
    anchor: datetime
    window_days: int = 7
    weeks: list[TrainingWeekSummary] = Field(default_factory=list)  # oldest first


class WeekDeltaTransition(BaseModel):
    baseline_window_start: datetime
    baseline_window_end: datetime
    current_window_start: datetime
    current_window_end: datetime

    activity_count: MetricComparison = Field(default_factory=MetricComparison)
    total_distance: MetricComparison = Field(default_factory=MetricComparison)
    total_calories: MetricComparison = Field(default_factory=MetricComparison)
    total_steps: MetricComparison = Field(default_factory=MetricComparison)
    total_ascent: MetricComparison = Field(default_factory=MetricComparison)
    total_descent: MetricComparison = Field(default_factory=MetricComparison)
    total_duration_seconds: MetricComparison = Field(default_factory=MetricComparison)
    average_heart_rate: MetricComparison = Field(default_factory=MetricComparison)
    max_heart_rate: MetricComparison = Field(default_factory=MetricComparison)


class TrainingTrendDelta(BaseModel):
    anchor: datetime
    window_days: int = 7
    transitions: list[WeekDeltaTransition] = Field(default_factory=list)  # oldest first


# --- Recovery (sleep / resting HR / HRV) ------------------------------------


class SleepRecord(BaseModel):
    record_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    go_bed_time: datetime | None = None
    fall_asleep_time: datetime | None = None
    wakeup_time: datetime | None = None

    light_sleep_minutes: int | None = None
    deep_sleep_minutes: int | None = None
    dream_sleep_minutes: int | None = None  # REM
    awake_minutes: int | None = None
    total_sleep_minutes: int | None = None

    sleep_score: int | None = None
    sleep_efficiency_percent: int | None = None
    sleep_latency_minutes: int | None = None
    wakeup_count: int | None = None
    deep_sleep_part: int | None = None  # undocumented by Huawei, passed through
    sleep_type: int | None = None  # undocumented by Huawei, passed through


class StatBlock(BaseModel):
    avg: float | None = None
    max: float | None = None
    min: float | None = None
    count: int | None = None


class DailyStat(BaseModel):
    day: str  # YYYY-MM-DD
    stats: StatBlock


class HealthMetricStats(BaseModel):
    """Server-side aggregates: overall window + one entry per day with data."""

    data_type: str
    field_name: str | None = None  # e.g. restBpm / avgHrv
    days: int
    start_day: str
    end_day: str
    overall: StatBlock = Field(default_factory=StatBlock)
    daily: list[DailyStat] = Field(default_factory=list)


# --- Performance ------------------------------------------------------------


class AthleticPerformance(BaseModel):
    """Huawei's running-ability indices (unitless) and predicted times (s)."""

    running_ability: float | None = None
    condition: float | None = None
    fitness: float | None = None
    fatigue: float | None = None
    ranking: float | None = None
    predicted_times: dict[str, float] = Field(default_factory=dict)


class PersonalBestEntry(BaseModel):
    name: str  # e.g. bestRunDistance (m), bestRunPartTime10KM (s)
    value: float | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None


class SportPersonalBests(BaseModel):
    activity_type: str
    personal_bests: list[PersonalBestEntry] = Field(default_factory=list)
