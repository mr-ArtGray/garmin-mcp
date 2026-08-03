"""Server assembly and transport selection.

The tool set is the table in ``tools.py`` plus the three tools below, which do
not fit the registry's three call shapes: one takes an ID, one fans out across
several endpoints, and one is a generic escape hatch.
"""

from __future__ import annotations

import logging
import secrets
import sys
from typing import Annotated, Any

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .client import GarminSession
from .config import ConfigError, Settings
from .dates import parse_date
from .registry import DateArg, DetailArg, register, report_failure, to_json
from .tools import CORE_TOOLS

__all__ = ["build_server", "main"]

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Read-only access to the signed-in user's own Garmin Connect health data.

Date arguments accept 'today', 'yesterday', relative offsets like '-7d', or an
explicit YYYY-MM-DD, so you never need to know the current date to ask for
recent data. Sleep is recorded against the morning it ends, so last night's
sleep is date='today'.

Results are trimmed by default: per-minute sample series are replaced with a
marker and long lists are truncated. Pass detail='full' only when individual
data points genuinely matter, as raw payloads can be very large.

For a general "how am I doing" question, prefer garmin_get_health_snapshot over
several single-metric calls. Endpoints without a dedicated tool are reachable
through garmin_api_request.
"""

_READ_ONLY = dict(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def build_server(session: GarminSession) -> MCPServer:
    """Construct the server and register every tool."""
    mcp: MCPServer = MCPServer(
        "garmin-health",
        title="Garmin Health",
        instructions=INSTRUCTIONS,
        version="0.1.0",
    )

    registered = register(mcp, session, CORE_TOOLS)

    @mcp.tool(
        name="garmin_get_activity",
        title="Activity detail",
        description=(
            "Get the full record of one recorded workout by its activity ID: type, "
            "timing, distance, pace and speed, heart rate zones, cadence, power, "
            "elevation, calories, training effect and recovery time. Find the ID "
            "with garmin_list_activities first."
        ),
        annotations=ToolAnnotations(title="Activity detail", **_READ_ONLY),
        structured_output=False,
    )
    async def garmin_get_activity(
        activity_id: Annotated[
            str,
            Field(
                description="Garmin activity ID, as returned in the 'activityId' "
                "field of garmin_list_activities."
            ),
        ],
        detail: DetailArg = "summary",
    ) -> str:
        with report_failure(
            "garmin_get_activity", activity_id=activity_id, detail=detail
        ):
            return to_json(await session.call("get_activity", str(activity_id)), detail)

    @mcp.tool(
        name="garmin_get_health_snapshot",
        title="Health snapshot",
        description=(
            "Get an all-in-one recovery picture for one day: daily summary, sleep, "
            "HRV, body battery and training readiness together. Prefer this for "
            "open questions like 'how did I sleep', 'how am I recovering' or 'should "
            "I train today' -- it answers in one call what would otherwise take five, "
            "and each metric is trimmed to its headline numbers. Metrics your device "
            "does not record are reported as errors within the result rather than "
            "failing the whole call."
        ),
        annotations=ToolAnnotations(title="Health snapshot", **_READ_ONLY),
        structured_output=False,
    )
    async def garmin_get_health_snapshot(date: DateArg = "today") -> str:
        with report_failure("garmin_get_health_snapshot", date=date):
            return await _health_snapshot(date)

    async def _health_snapshot(date: str) -> str:
        day = parse_date(date)
        results: dict[str, Any] = {}

        async def fetch(key: str, method: str, *args: Any) -> None:
            try:
                results[key] = await session.call(method, *args)
            except Exception as exc:  # noqa: BLE001
                # One unsupported metric should not sink the other four; devices
                # differ widely in what they record.
                results[key] = {"_error": str(exc)}

        async with anyio.create_task_group() as tg:
            tg.start_soon(fetch, "daily_summary", "get_user_summary", day)
            tg.start_soon(fetch, "sleep", "get_sleep_data", day)
            tg.start_soon(fetch, "hrv", "get_hrv_data", day)
            tg.start_soon(fetch, "body_battery", "get_body_battery", day, day)
            tg.start_soon(fetch, "training_readiness", "get_training_readiness", day)

        # If every metric failed the cause is shared -- usually expired tokens --
        # so report it once as a real error instead of five buried copies.
        errors = [
            v["_error"]
            for v in results.values()
            if isinstance(v, dict) and "_error" in v
        ]
        if len(errors) == len(results):
            raise ToolError(errors[0])

        return to_json({"date": day, **results})

    @mcp.tool(
        name="garmin_api_request",
        title="Raw Garmin API request",
        description=(
            "Call any Garmin Connect API path directly. This is the escape hatch for "
            "the ~130 endpoints without a dedicated tool (SpO2, respiration, VO2 max, "
            "race predictions, gear, workouts, badges, and so on). Prefer a dedicated "
            "tool when one exists -- their output is easier to read and their "
            "arguments are validated. Paths look like "
            "'/usersummary-service/stats/daily/2026-08-01/2026-08-03'; the response is "
            "returned as-is."
        ),
        annotations=ToolAnnotations(title="Raw Garmin API request", **_READ_ONLY),
        structured_output=False,
    )
    async def garmin_api_request(
        path: Annotated[
            str,
            Field(
                description="Garmin Connect API path, beginning with '/', e.g. "
                "'/wellness-service/wellness/dailySpo2/2026-08-01'."
            ),
        ],
        params: Annotated[
            dict[str, Any] | None,
            Field(description="Optional query string parameters."),
        ] = None,
        detail: DetailArg = "summary",
    ) -> str:
        with report_failure("garmin_api_request", path=path, params=params):
            clean = path.strip()
            if not clean:
                raise ToolError(
                    "path is required, e.g. '/userprofile-service/userprofile'"
                )
            if clean.startswith(("http://", "https://")):
                raise ToolError(
                    "Pass a path, not a full URL -- for example "
                    "'/userprofile-service/userprofile'. The host is added for you."
                )
            if not clean.startswith("/"):
                clean = "/" + clean

            kwargs: dict[str, Any] = {"params": params} if params else {}
            return to_json(await session.call("connectapi", clean, **kwargs), detail)

    logger.info(
        "Registered %d tools", len(registered) + 3
    )
    return mcp


class _BearerAuth:
    """Minimal ASGI guard for the HTTP transport.

    The SDK's ``AuthSettings`` implements a full OAuth resource server, which is
    more than a single-user personal deployment needs. A shared secret is enough
    here, and leaving ``AuthSettings``/``token_verifier`` untouched means the
    upgrade to real OAuth later is additive rather than a rewrite.
    """

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        supplied = ""
        for key, value in scope.get("headers", ()):
            if key == b"authorization":
                supplied = value.decode("latin-1")
                break

        # Constant-time compare so the secret cannot be recovered byte by byte.
        if not secrets.compare_digest(supplied, self._expected):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b'Bearer realm="garmin-mcp"'),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"error":"missing or invalid bearer token"}',
                }
            )
            return

        await self._app(scope, receive, send)


def _serve_http(mcp: MCPServer, settings: Settings) -> None:
    import uvicorn

    # Stateless with plain JSON responses: no session affinity, so the server can
    # be restarted or replicated without clients noticing.
    app = mcp.streamable_http_app(
        json_response=True,
        stateless_http=True,
        host=settings.host,
    )
    assert settings.auth_token is not None  # guaranteed by Settings.from_env
    guarded = _BearerAuth(app, settings.auth_token)

    logger.info(
        "Serving Garmin MCP on http://%s:%d/mcp (bearer auth required)",
        settings.host,
        settings.port,
    )
    uvicorn.run(
        guarded,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


def main() -> None:
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        # stdout carries JSON-RPC frames on the stdio transport; diagnostics must
        # never go there or they corrupt the stream.
        print(f"garmin-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2) from None

    logging.basicConfig(
        level=settings.log_level,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )

    session = GarminSession(settings)
    mcp = build_server(session)

    if settings.is_http:
        _serve_http(mcp, settings)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
