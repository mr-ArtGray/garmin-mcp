"""Shared fixtures.

Every test runs fully offline: no Garmin credentials, no network. The fake
session below stands in for :class:`~garmin_mcp.client.GarminSession` and
records what the tools asked for, which is what the registry tests assert on.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from garmin_mcp import dates


class FakeSession:
    """Stand-in for GarminSession that records calls instead of making them."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.errors: dict[str, Exception] = {}

    async def call(self, method: str, /, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((method, args, kwargs))
        if method in self.errors:
            raise self.errors[method]
        return self.responses.get(method, {"method": method, "args": list(args)})

    def last(self) -> tuple[str, tuple[Any, ...], dict[str, Any]]:
        return self.calls[-1]


@pytest.fixture
def anyio_backend() -> str:
    """Run @pytest.mark.anyio tests on asyncio only (anyio ships the plugin)."""
    return "asyncio"


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def frozen_today(monkeypatch: pytest.MonkeyPatch) -> date:
    """Pin 'today' so relative-date assertions are stable.

    Chosen mid-month and mid-year so month/year offsets cannot accidentally pass
    by landing on a boundary.
    """
    fixed = date(2026, 8, 3)
    monkeypatch.setattr(dates, "today", lambda: fixed)
    return fixed
