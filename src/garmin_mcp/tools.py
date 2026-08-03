"""The tool table.

To expose another Garmin endpoint, add a row. The registry supplies date
parsing, payload trimming, read-only annotations and error handling, so nothing
else needs to change.

The ``garminconnect`` client has roughly 140 public methods; the ones below are
the everyday health metrics. Anything not listed is still reachable through the
``garmin_api_request`` escape hatch, which is the intended way to try an
endpoint before deciding it deserves a row here.

Descriptions are written for a model choosing between tools, so each says what
comes back and when to prefer it over a neighbour.
"""

from __future__ import annotations

from .registry import Shape, ToolSpec

__all__ = ["CORE_TOOLS"]

CORE_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="garmin_get_daily_summary",
        method="get_user_summary",
        title="Daily summary",
        shape=Shape.DAILY,
        description=(
            "Get the all-round wellness summary for one day: total and goal steps, "
            "distance, floors, calories (active, resting, consumed), intensity "
            "minutes, resting heart rate, average stress, and body battery high/low. "
            "Start here for broad questions like 'how active was I on Tuesday' -- it "
            "answers in one call what would otherwise take several."
        ),
    ),
    ToolSpec(
        name="garmin_get_sleep",
        method="get_sleep_data",
        title="Sleep",
        shape=Shape.DAILY,
        description=(
            "Get sleep for the night ENDING on the given date: overall sleep score "
            "and its component ratings, time in deep/light/REM/awake, sleep and wake "
            "times, average overnight SpO2, respiration and resting heart rate. "
            "Ask for 'today' to get last night. With detail='full' this also returns "
            "per-minute sleep stage, movement and heart rate series, which are large."
        ),
    ),
    ToolSpec(
        name="garmin_get_hrv",
        method="get_hrv_data",
        title="Heart rate variability",
        shape=Shape.DAILY,
        description=(
            "Get overnight heart rate variability for one day: the weekly average, "
            "last night's average, the personal baseline range, and Garmin's HRV "
            "status (balanced, unbalanced, low, poor). HRV is the most sensitive "
            "single indicator of accumulated stress or illness, so prefer this when "
            "asked about recovery trends rather than a single night's sleep."
        ),
    ),
    ToolSpec(
        name="garmin_get_stress",
        method="get_stress_data",
        title="Stress",
        shape=Shape.DAILY,
        description=(
            "Get stress for one day: the average and maximum stress score, plus how "
            "the day divided between rest, low, medium and high stress. Use "
            "detail='full' for the underlying per-interval stress series."
        ),
    ),
    ToolSpec(
        name="garmin_get_heart_rate",
        method="get_heart_rates",
        title="Heart rate",
        shape=Shape.DAILY,
        description=(
            "Get heart rate for one day: resting, minimum and maximum heart rate, and "
            "the last seven days' resting average. Use detail='full' for the "
            "continuous all-day heart rate series. For heart rate during a specific "
            "workout, use garmin_get_activity instead."
        ),
    ),
    ToolSpec(
        name="garmin_get_training_readiness",
        method="get_training_readiness",
        title="Training readiness",
        shape=Shape.DAILY,
        description=(
            "Get Garmin's training readiness score for one day (0-100) together with "
            "the factors behind it: sleep score and history, recovery time, HRV "
            "status, acute training load and stress history. This is the tool for "
            "'should I train hard today' -- it is Garmin's own synthesis of the "
            "recovery signals rather than a raw metric."
        ),
    ),
    ToolSpec(
        name="garmin_get_body_battery",
        method="get_body_battery",
        title="Body battery",
        shape=Shape.RANGE,
        description=(
            "Get body battery energy levels across a date range: the daily high and "
            "low, plus charge and drain totals. Body battery rises with rest and "
            "falls with activity and stress, so a multi-day range shows whether "
            "energy is trending down faster than it recovers."
        ),
    ),
    ToolSpec(
        name="garmin_get_steps",
        method="get_daily_steps",
        title="Daily steps",
        shape=Shape.RANGE,
        description=(
            "Get daily step totals, step goal and distance for each day in a range. "
            "Cheap and compact -- prefer this over repeated daily-summary calls when "
            "the question is only about step counts over time."
        ),
    ),
    ToolSpec(
        name="garmin_list_activities",
        method="get_activities_by_date",
        title="List activities",
        shape=Shape.RANGE,
        description=(
            "List recorded workouts in a date range, newest first, with activity ID, "
            "type, name, start time, duration, distance, pace, average and max heart "
            "rate, calories, elevation and training effect. Use this to find an "
            "activity, then pass its activityId to garmin_get_activity for the full "
            "detail of a single session."
        ),
    ),
    ToolSpec(
        name="garmin_get_body_composition",
        method="get_body_composition",
        title="Body composition",
        shape=Shape.RANGE,
        description=(
            "Get weigh-ins over a date range: weight, BMI, body fat and water "
            "percentage, muscle and bone mass, plus the range averages. Only returns "
            "data for days with a recorded weigh-in, so an empty result usually means "
            "no measurement rather than an error."
        ),
    ),
    ToolSpec(
        name="garmin_get_user_profile",
        method="get_user_profile",
        title="User profile",
        shape=Shape.NONE,
        description=(
            "Get the account profile: name, birth date, height, weight, gender, "
            "activity level and -- importantly -- the measurement system. Worth "
            "checking once before interpreting units in other tools, since Garmin "
            "returns metric values regardless of what the app displays."
        ),
    ),
)
