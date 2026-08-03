"""Payload reduction.

Garmin responses carry per-minute time series. A single ``get_sleep_data`` call
returns sleep levels, movement, respiration, HRV and body-battery samples for
the whole night and can exceed 100KB of JSON -- enough to swamp a context
window in one tool call, for data that is almost never what was asked for.

The reduction here is deliberately *generic* rather than a hand-written
schema per endpoint: Garmin adds and renames fields without notice, and the
``garmin_api_request`` escape hatch can return any shape at all. So the rules
are structural (drop known sample series, truncate long lists) and degrade
safely on payloads this code has never seen.

Callers that genuinely want the time series pass ``detail="full"``, which
skips all of this.
"""

from __future__ import annotations

from typing import Any

__all__ = ["condense", "DEFAULT_MAX_LIST"]

DEFAULT_MAX_LIST = 8
"""Lists longer than this are truncated with a marker noting the original length."""

# Keys whose values are raw sample series. These are dropped outright rather
# than truncated: a handful of samples from a night of data is noise, and the
# count alone is more useful than an arbitrary prefix.
_SERIES_KEYS = frozenset(
    {
        "bodyBatteryValuesArray",
        "bodyBatteryValueDescriptorDTOList",
        "heartRateValues",
        "heartRateValueDescriptors",
        "hrvReadings",
        "respirationValuesArray",
        "respirationValueDescriptorsDTOList",
        "sleepHeartRate",
        "sleepBodyBattery",
        "sleepLevels",
        "sleepMovement",
        "sleepRestlessMoments",
        "sleepStress",
        "spO2HourlyAverages",
        "stressValuesArray",
        "stressValueDescriptorsDTOList",
        "wellnessEpochRespirationDataDTOList",
        "wellnessEpochSpO2DataDTOList",
        "wellnessEpochRespirationAveragesList",
    }
)


def _omitted(count: int, what: str = "items") -> dict[str, Any]:
    return {
        "_omitted": f"{count} {what} omitted; call again with detail='full' to include them"
    }


def condense(value: Any, *, max_list: int = DEFAULT_MAX_LIST, _depth: int = 0) -> Any:
    """Recursively shrink a decoded JSON payload.

    Structure is preserved so the shape stays recognisable -- only the bulk is
    removed, and every removal leaves a marker saying how to get it back.
    """
    # Guard against pathological nesting rather than blowing the stack.
    if _depth > 12:
        return value

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in _SERIES_KEYS:
                if isinstance(item, list):
                    if item:
                        out[key] = _omitted(len(item), "samples")
                    else:
                        out[key] = []
                elif item is not None:
                    out[key] = _omitted(1, "samples")
                continue
            out[key] = condense(item, max_list=max_list, _depth=_depth + 1)
        return out

    if isinstance(value, list):
        if len(value) > max_list:
            head = [
                condense(item, max_list=max_list, _depth=_depth + 1)
                for item in value[:max_list]
            ]
            return [*head, _omitted(len(value) - max_list, "more items")]
        return [condense(item, max_list=max_list, _depth=_depth + 1) for item in value]

    return value
