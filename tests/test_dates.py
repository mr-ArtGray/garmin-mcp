from __future__ import annotations

import pytest

from garmin_mcp.dates import DateError, parse_date, parse_range


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("today", "2026-08-03"),
        ("TODAY", "2026-08-03"),
        ("  Today  ", "2026-08-03"),
        ("now", "2026-08-03"),
        ("yesterday", "2026-08-02"),
        ("tomorrow", "2026-08-04"),
        ("-1d", "2026-08-02"),
        ("-7d", "2026-07-27"),
        ("-2w", "2026-07-20"),
        ("+3d", "2026-08-06"),
        ("-1y", "2025-08-03"),
        ("2026-01-15", "2026-01-15"),
    ],
)
def test_parse_date_accepts_relative_and_absolute(
    value: str, expected: str, frozen_today
) -> None:
    assert parse_date(value) == expected


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_means_today(value: str | None, frozen_today) -> None:
    """Tools default to 'most recent data' without the caller knowing the date."""
    assert parse_date(value) == "2026-08-03"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Unsigned offsets: a model writes '7d' at least as often as '-7d', and
        # future health data does not exist, so this must mean the past.
        ("7d", "2026-07-27"),
        ("30d", "2026-07-04"),
        ("1w", "2026-07-27"),
        # Natural-language forms a model produces unprompted.
        ("7 days ago", "2026-07-27"),
        ("2 weeks ago", "2026-07-20"),
        ("a week ago", "2026-07-27"),
        ("a month ago", "2026-07-04"),
        ("last week", "2026-07-27"),
        ("last month", "2026-07-04"),
        # Sleep is filed against the morning it ends.
        ("last night", "2026-08-03"),
        ("tonight", "2026-08-03"),
        # Timestamps and slash dates.
        ("2026-08-02T00:00:00Z", "2026-08-02"),
        ("2026-08-02T22:15:00", "2026-08-02"),
        ("2026-08-02 22:15:00", "2026-08-02"),
        ("2026/08/02", "2026-08-02"),
        # Spacing and case should never decide whether a call succeeds.
        ("Last  Night", "2026-08-03"),
        ("-7 d", "2026-07-27"),
        ("2 WEEKS AGO", "2026-07-20"),
    ],
)
def test_forms_a_model_actually_produces_are_accepted(
    value: str, expected: str, frozen_today
) -> None:
    """Regression: a rejected argument fails before any network call, which is
    invisible in the response and was mistaken for an authentication failure."""
    assert parse_date(value) == expected


def test_explicit_plus_still_looks_forward(frozen_today) -> None:
    assert parse_date("+3d") == "2026-08-06"


@pytest.mark.parametrize("value", ["last tuesday", "07/15/2026", "-3", "d7", "2026-13-01"])
def test_unparseable_dates_explain_the_syntax(value: str) -> None:
    with pytest.raises(DateError) as excinfo:
        parse_date(value)
    assert "YYYY-MM-DD" in str(excinfo.value)


def test_error_names_the_offending_field() -> None:
    with pytest.raises(DateError, match="start_date"):
        parse_range("nonsense", "today")


def test_error_echoes_what_was_received() -> None:
    """The operator needs the rejected value to diagnose it from a log line."""
    with pytest.raises(DateError, match="last tuesday"):
        parse_date("last tuesday")


def test_range_defaults_end_to_today(frozen_today) -> None:
    assert parse_range("-7d", None) == ("2026-07-27", "2026-08-03")


def test_single_day_range_is_allowed(frozen_today) -> None:
    assert parse_range("today", "today") == ("2026-08-03", "2026-08-03")


def test_reversed_range_is_rejected_not_swapped(frozen_today) -> None:
    """Silently swapping would answer a different question than the one asked."""
    with pytest.raises(DateError) as excinfo:
        parse_range("today", "-7d")
    assert "after end_date" in str(excinfo.value)
