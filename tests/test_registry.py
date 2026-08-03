"""Tests for the expansion seam.

These cover the machinery that every current and future tool row depends on:
that each shape forwards the right arguments, that dates are normalised before
they reach Garmin, and that trimming is applied unless explicitly waived.
"""

from __future__ import annotations

import json

import pytest
from mcp.server import MCPServer

from garmin_mcp.registry import Shape, ToolSpec, _build, register

pytestmark = pytest.mark.anyio


def spec(shape: Shape, method: str = "get_thing", **kw) -> ToolSpec:
    return ToolSpec(
        name=f"garmin_{method}",
        method=method,
        title="Thing",
        description="A thing.",
        shape=shape,
        **kw,
    )


async def test_none_shape_calls_with_no_arguments(session) -> None:
    tool = _build(spec(Shape.NONE), session)
    await tool()
    assert session.last() == ("get_thing", (), {})


async def test_daily_shape_forwards_a_normalised_date(session, frozen_today) -> None:
    tool = _build(spec(Shape.DAILY), session)
    await tool(date="yesterday")
    assert session.last() == ("get_thing", ("2026-08-02",), {})


async def test_daily_shape_defaults_to_today(session, frozen_today) -> None:
    tool = _build(spec(Shape.DAILY), session)
    await tool()
    assert session.last() == ("get_thing", ("2026-08-03",), {})


async def test_range_shape_forwards_start_and_end_positionally(
    session, frozen_today
) -> None:
    tool = _build(spec(Shape.RANGE), session)
    await tool(start_date="2026-07-01", end_date="2026-07-31")
    assert session.last() == ("get_thing", ("2026-07-01", "2026-07-31"), {})


async def test_range_shape_uses_the_spec_default_window(session, frozen_today) -> None:
    """A range tool called with no arguments must still return useful data."""
    tool = _build(spec(Shape.RANGE, default_start="-30d"), session)
    await tool()
    assert session.last() == ("get_thing", ("2026-07-04", "2026-08-03"), {})


async def test_invalid_dates_fail_before_reaching_garmin(session) -> None:
    tool = _build(spec(Shape.DAILY), session)
    with pytest.raises(Exception, match="YYYY-MM-DD"):
        await tool(date="last thursday")
    assert session.calls == []


async def test_failures_are_logged_with_their_arguments(session, caplog) -> None:
    """Without this, a rejected argument leaves no trace in the server log."""
    tool = _build(spec(Shape.DAILY), session)

    with caplog.at_level("WARNING"), pytest.raises(Exception):
        await tool(date="last tuesday")

    assert "garmin_get_thing" in caplog.text
    assert "last tuesday" in caplog.text


async def test_summary_is_trimmed_and_full_is_not(session) -> None:
    session.responses["get_thing"] = {"heartRateValues": [[1, 60]] * 200}
    tool = _build(spec(Shape.DAILY), session)

    trimmed = json.loads(await tool(detail="summary"))
    assert "_omitted" in trimmed["heartRateValues"]

    raw = json.loads(await tool(detail="full"))
    assert len(raw["heartRateValues"]) == 200


async def test_list_payloads_survive_serialisation(session) -> None:
    """Garmin returns arrays for some metrics and objects for others."""
    session.responses["get_thing"] = [{"steps": 1}, {"steps": 2}]
    tool = _build(spec(Shape.RANGE), session)
    assert json.loads(await tool()) == [{"steps": 1}, {"steps": 2}]


async def test_registered_tools_are_marked_read_only(session) -> None:
    mcp: MCPServer = MCPServer("test")
    names = register(mcp, session, [spec(Shape.DAILY), spec(Shape.RANGE, "get_other")])

    assert names == ["garmin_get_thing", "garmin_get_other"]
    for tool in await mcp.list_tools():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False


async def test_registration_produces_a_usable_schema(session) -> None:
    mcp: MCPServer = MCPServer("test")
    register(mcp, session, [spec(Shape.RANGE)])
    tool = (await mcp.list_tools())[0]

    props = tool.input_schema["properties"]
    assert set(props) == {"start_date", "end_date", "detail"}
    assert props["detail"]["enum"] == ["summary", "full"]
    assert tool.description == "A thing."


async def test_end_to_end_call_through_the_server(session, frozen_today) -> None:
    """Guards the wiring between add_tool and the generated closure."""
    session.responses["get_thing"] = {"sleepScore": 91}
    mcp: MCPServer = MCPServer("test")
    register(mcp, session, [spec(Shape.DAILY)])

    result = await mcp.call_tool("garmin_get_thing", {"date": "today"})

    assert session.last() == ("get_thing", ("2026-08-03",), {})
    assert "91" in result.content[0].text
