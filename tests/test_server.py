from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from garmin_mcp.server import _BearerAuth, build_server
from garmin_mcp.tools import CORE_TOOLS

pytestmark = pytest.mark.anyio


async def call(mcp, name: str, **args) -> str:
    result = await mcp.call_tool(name, args)
    return result.content[0].text


async def test_every_tool_is_registered_and_read_only(session) -> None:
    mcp = build_server(session)
    tools = await mcp.list_tools()

    assert len(tools) == len(CORE_TOOLS) + 3
    for tool in tools:
        assert tool.name.startswith("garmin_")
        assert tool.description, f"{tool.name} has no description"
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True


async def test_tool_names_are_unique(session) -> None:
    names = [t.name for t in await build_server(session).list_tools()]
    assert len(names) == len(set(names))


async def test_specs_point_at_real_garmin_methods() -> None:
    """Catches a typo'd or renamed method when a row is added to the table."""
    from garminconnect import Garmin

    for spec in CORE_TOOLS:
        assert hasattr(Garmin, spec.method), f"{spec.name} -> Garmin.{spec.method}"


async def test_get_activity_passes_the_id_through(session) -> None:
    mcp = build_server(session)
    await call(mcp, "garmin_get_activity", activity_id="1234567890")
    assert session.last() == ("get_activity", ("1234567890",), {})


async def test_health_snapshot_fans_out_across_metrics(session, frozen_today) -> None:
    mcp = build_server(session)
    payload = json.loads(await call(mcp, "garmin_get_health_snapshot", date="today"))

    assert payload["date"] == "2026-08-03"
    assert set(payload) == {
        "date",
        "daily_summary",
        "sleep",
        "hrv",
        "body_battery",
        "training_readiness",
    }
    assert {c[0] for c in session.calls} == {
        "get_user_summary",
        "get_sleep_data",
        "get_hrv_data",
        "get_body_battery",
        "get_training_readiness",
    }


async def test_health_snapshot_survives_one_unsupported_metric(session) -> None:
    """Devices differ in what they record; one gap must not sink the call."""
    session.errors["get_hrv_data"] = ToolError("no HRV on this device")
    mcp = build_server(session)

    payload = json.loads(await call(mcp, "garmin_get_health_snapshot"))

    assert "no HRV on this device" in payload["hrv"]["_error"]
    assert "_error" not in payload["sleep"]


async def test_health_snapshot_reports_a_shared_failure_once(session) -> None:
    """All five failing means one cause -- usually expired tokens."""
    for method in (
        "get_user_summary",
        "get_sleep_data",
        "get_hrv_data",
        "get_body_battery",
        "get_training_readiness",
    ):
        session.errors[method] = ToolError("token store expired")

    mcp = build_server(session)
    with pytest.raises(ToolError, match="token store expired"):
        await mcp.call_tool("garmin_get_health_snapshot", {})


async def test_api_request_normalises_a_missing_leading_slash(session) -> None:
    mcp = build_server(session)
    await call(mcp, "garmin_api_request", path="userprofile-service/userprofile")
    assert session.last() == ("connectapi", ("/userprofile-service/userprofile",), {})


async def test_api_request_forwards_query_params(session) -> None:
    mcp = build_server(session)
    await call(mcp, "garmin_api_request", path="/x", params={"limit": 5})
    assert session.last() == ("connectapi", ("/x",), {"params": {"limit": 5}})


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("https://connect.garmin.com/x", "Pass a path, not a full URL"),
        ("   ", "path is required"),
    ],
)
async def test_api_request_rejects_bad_paths(session, path: str, expected: str) -> None:
    mcp = build_server(session)
    with pytest.raises(ToolError, match=expected):
        await mcp.call_tool("garmin_api_request", {"path": path})
    assert session.calls == []


# --- HTTP bearer guard ---------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.reached = False

    async def __call__(self, scope, receive, send) -> None:
        self.reached = True


async def _probe(header: bytes | None) -> tuple[bool, int | None]:
    """Drive the guard directly; returns (app was reached, response status)."""
    downstream = _Recorder()
    guard = _BearerAuth(downstream, "s3cret")

    headers = [] if header is None else [(b"authorization", header)]
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    await guard({"type": "http", "headers": headers}, None, send)
    status = sent[0]["status"] if sent else None
    return downstream.reached, status


async def test_correct_bearer_token_is_allowed_through() -> None:
    reached, status = await _probe(b"Bearer s3cret")
    assert reached is True
    assert status is None


@pytest.mark.parametrize(
    "header",
    [
        None,
        b"",
        b"Bearer wrong",
        b"s3cret",  # scheme omitted
        b"Basic czNjcmV0",
        b"bearer s3cret",  # scheme is case-sensitive here
    ],
)
async def test_bad_credentials_get_401_without_reaching_the_app(
    header: bytes | None,
) -> None:
    reached, status = await _probe(header)
    assert reached is False
    assert status == 401


async def test_non_http_scopes_bypass_the_guard() -> None:
    """Lifespan messages are not requests and carry no headers."""
    downstream = _Recorder()
    guard = _BearerAuth(downstream, "s3cret")
    await guard({"type": "lifespan"}, None, None)
    assert downstream.reached is True
