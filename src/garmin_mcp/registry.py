"""The expansion seam.

Almost every readable Garmin endpoint has one of three call shapes: no
arguments, a single date, or a start/end pair. Rather than hand-write a tool
function per endpoint -- and repeat date parsing, payload trimming, and error
handling a hundred times -- a :class:`ToolSpec` describes an endpoint as data
and :func:`register` turns it into a fully wired MCP tool.

Adding an endpoint is therefore one row in ``tools.py``. Endpoints that do not
fit the three shapes (anything taking an ID, or fanning out across several
calls) are written as ordinary ``@mcp.tool()`` functions alongside these.

Responses are returned as JSON *text* rather than structured content. Garmin's
payloads are undocumented, change without notice, and vary between object and
array for the same conceptual metric; committing to an output schema would turn
every upstream change into a runtime validation failure instead of a field the
model can simply ignore.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import Enum, auto
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .client import GarminSession
from .dates import parse_date, parse_range
from .trim import condense

__all__ = ["Shape", "ToolSpec", "register", "report_failure", "to_json"]

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def report_failure(tool: str, **arguments: Any) -> Iterator[None]:
    """Log a failing tool call, with the arguments that caused it, then re-raise.

    MCP clients show the error text to the model but do not record the arguments
    anywhere the operator can see, and a rejected argument fails before any
    network call -- so without this a bad-input failure leaves no trace at all in
    the server log and is near-impossible to diagnose after the fact.
    """
    try:
        yield
    except Exception as exc:
        logger.warning(
            "tool %s failed: %s: %s (arguments=%r)",
            tool,
            type(exc).__name__,
            exc,
            arguments,
        )
        raise


class Shape(Enum):
    """How the underlying ``garminconnect`` method takes its arguments."""

    NONE = auto()
    """``method()`` -- settings and profile style endpoints."""

    DAILY = auto()
    """``method(cdate)`` -- one calendar day."""

    RANGE = auto()
    """``method(startdate, enddate)`` -- an inclusive span of days."""


Detail = Literal["summary", "full"]

_DATE_HELP = (
    "Date as YYYY-MM-DD, or relative: 'today', 'yesterday', '-7d', '-2w', '-3m'."
)
_DETAIL_HELP = (
    "'summary' (default) drops per-minute sample series and truncates long lists, "
    "which is what you want for almost every question. 'full' returns Garmin's raw "
    "payload including every sample -- accurate but potentially enormous, so use it "
    "only when the individual data points matter."
)

DateArg = Annotated[str, Field(description=_DATE_HELP)]
DetailArg = Annotated[Detail, Field(description=_DETAIL_HELP)]


@dataclass(frozen=True)
class ToolSpec:
    """A Garmin endpoint, described as data.

    ``name`` is what the model sees; ``method`` is the attribute looked up on the
    ``garminconnect`` client at call time, so a spec referring to a method that a
    given library version does not have fails with a clear message rather than at
    import.
    """

    name: str
    method: str
    title: str
    description: str
    shape: Shape
    default_start: str = "-7d"
    """Range tools only: how far back to look when the caller gives no start date."""


def to_json(payload: Any, detail: Detail = "summary") -> str:
    """Serialise a Garmin payload for return to the model."""
    data = payload if detail == "full" else condense(payload)
    # default=str keeps datetimes and other stray non-JSON types from failing the
    # whole call; ensure_ascii=False avoids mangling non-English activity names.
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _build(spec: ToolSpec, session: GarminSession) -> Callable[..., Any]:
    """Create the tool function for a spec.

    The returned function needs real annotated parameters, because the SDK derives
    the tool's input schema from its signature.
    """
    if spec.shape is Shape.NONE:

        async def tool(detail: DetailArg = "summary") -> str:
            with report_failure(spec.name, detail=detail):
                return to_json(await session.call(spec.method), detail)

    elif spec.shape is Shape.DAILY:

        async def tool(  # type: ignore[misc]
            date: DateArg = "today",
            detail: DetailArg = "summary",
        ) -> str:
            with report_failure(spec.name, date=date, detail=detail):
                payload = await session.call(spec.method, parse_date(date))
                return to_json(payload, detail)

    elif spec.shape is Shape.RANGE:

        async def tool(  # type: ignore[misc]
            start_date: DateArg = spec.default_start,
            end_date: DateArg = "today",
            detail: DetailArg = "summary",
        ) -> str:
            with report_failure(
                spec.name, start_date=start_date, end_date=end_date, detail=detail
            ):
                start, end = parse_range(start_date, end_date)
                payload = await session.call(spec.method, start, end)
                return to_json(payload, detail)

    else:  # pragma: no cover - guards against an unhandled enum member
        raise ValueError(f"Unsupported shape {spec.shape!r} for tool {spec.name!r}")

    tool.__name__ = spec.name
    tool.__doc__ = spec.description
    return tool


def register(
    mcp: MCPServer, session: GarminSession, specs: Iterable[ToolSpec]
) -> list[str]:
    """Register every spec as a read-only MCP tool. Returns the names registered."""
    names: list[str] = []
    for spec in specs:
        mcp.add_tool(
            _build(spec, session),
            name=spec.name,
            title=spec.title,
            description=spec.description,
            annotations=ToolAnnotations(
                title=spec.title,
                read_only_hint=True,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=True,
            ),
            structured_output=False,
        )
        names.append(spec.name)
    return names
