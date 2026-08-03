"""Date parsing for tool arguments.

Garmin's API only speaks ``YYYY-MM-DD``. An LLM calling these tools often does
not reliably know today's date, so every date argument also accepts relative
forms (``today``, ``yesterday``, ``-7d``). Everything funnels through
:func:`parse_date` so the accepted syntax is identical on every tool.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

__all__ = ["DateError", "parse_date", "parse_range", "today"]

# A model writes dates the way a person would, so the accepted syntax is
# deliberately wide. Anything rejected here fails the tool call before it
# reaches Garmin, which is invisible in the response and expensive to debug --
# being permissive is worth far more than being strict.

# '-7d', '+2w', and bare '7d'. An unsigned offset means the past: these are
# historical health metrics, so a future date is never the intent.
_OFFSET = re.compile(r"^([+-]?)\s*(\d+)\s*(d|w|m|y|day|days|week|weeks|month|months|year|years)$")

# '7 days ago', 'a week ago'
_AGO = re.compile(r"^(?:(\d+)|an?)\s*(d|w|m|y|day|days|week|weeks|month|months|year|years)\s+ago$")

_KEYWORDS = {
    "today": 0,
    "now": 0,
    "tonight": 0,
    # Sleep is filed against the morning it ends, so "last night" is today's
    # record -- see the server instructions.
    "last night": 0,
    "lastnight": 0,
    "yesterday": -1,
    "tomorrow": 1,
    "last week": -7,
    "lastweek": -7,
    "a week ago": -7,
    "last month": -30,
    "lastmonth": -30,
    "last year": -365,
}

_UNIT_DAYS = {
    "d": 1, "day": 1, "days": 1,
    "w": 7, "week": 7, "weeks": 7,
    "m": 30, "month": 30, "months": 30,
    "y": 365, "year": 365, "years": 365,
}

_SYNTAX_HELP = (
    "Expected YYYY-MM-DD, a keyword like 'today'/'yesterday'/'last night', or a "
    "relative offset like '-7d', '2 weeks ago', 'last month'."
)


class DateError(ValueError):
    """Raised when a date argument cannot be interpreted."""


def today() -> date:
    """Indirection point so tests can reason about 'now' without patching stdlib."""
    return date.today()


def parse_date(value: str | None, *, field: str = "date") -> str:
    """Normalise a user/LLM supplied date into ``YYYY-MM-DD``.

    ``None`` and the empty string mean today, so tools can default to "the most
    recent data" without the caller needing to know the date.
    """
    if value is None:
        return today().isoformat()

    # Collapse internal whitespace so 'last  night' and 'last night' agree.
    raw = " ".join(value.strip().lower().split())
    if not raw:
        return today().isoformat()

    if raw in _KEYWORDS:
        return (today() + timedelta(days=_KEYWORDS[raw])).isoformat()

    match = _OFFSET.match(raw)
    if match:
        sign, amount, unit = match.groups()
        days = int(amount) * _UNIT_DAYS[unit]
        # Bare '7d' means seven days back; only an explicit '+' looks forward.
        if sign != "+":
            days = -days
        return (today() + timedelta(days=days)).isoformat()

    match = _AGO.match(raw)
    if match:
        amount, unit = match.groups()
        days = int(amount or 1) * _UNIT_DAYS[unit]
        return (today() - timedelta(days=days)).isoformat()

    # Tolerate slash-separated and full ISO timestamps, both of which models
    # produce readily: '2026/08/02', '2026-08-02T00:00:00Z'.
    candidate = raw.replace("/", "-")
    if "t" in candidate:
        candidate = candidate.split("t", 1)[0]
    if " " in candidate:
        candidate = candidate.split(" ", 1)[0]

    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        raise DateError(f"Could not parse {field}={value!r}. {_SYNTAX_HELP}") from None


def parse_range(start: str | None, end: str | None) -> tuple[str, str]:
    """Normalise a start/end pair, defaulting the end to today.

    Swapping a reversed range would silently answer a question the caller did not
    ask, so this reports the mistake instead.
    """
    start_iso = parse_date(start, field="start_date")
    end_iso = parse_date(end, field="end_date")
    if start_iso > end_iso:
        raise DateError(
            f"start_date ({start_iso}) is after end_date ({end_iso}). "
            "Pass the earlier date as start_date."
        )
    return start_iso, end_iso
