"""Environment-driven configuration.

Everything that differs between "running locally over stdio" and "deployed
somewhere reachable" is read here, so switching between the two is a matter of
environment variables rather than code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

__all__ = ["ConfigError", "Settings", "STDIO", "HTTP"]

STDIO = "stdio"
HTTP = "streamable-http"

_TRANSPORTS = (STDIO, HTTP)
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

DEFAULT_TOKEN_STORE = "~/.garminconnect"


class ConfigError(RuntimeError):
    """Raised when the environment describes a server that should not start."""


@dataclass(frozen=True)
class Settings:
    transport: Literal["stdio", "streamable-http"]
    host: str
    port: int
    token_store: str
    auth_token: str | None
    log_level: str

    @property
    def is_http(self) -> bool:
        return self.transport == HTTP

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        src = os.environ if env is None else env

        transport = src.get("GARMIN_MCP_TRANSPORT", STDIO).strip().lower()
        if transport not in _TRANSPORTS:
            raise ConfigError(
                f"GARMIN_MCP_TRANSPORT={transport!r} is not valid. "
                f"Use one of: {', '.join(_TRANSPORTS)}."
            )

        port_raw = src.get("GARMIN_MCP_PORT", "8000").strip()
        try:
            port = int(port_raw)
        except ValueError:
            raise ConfigError(
                f"GARMIN_MCP_PORT={port_raw!r} is not an integer."
            ) from None
        if not 1 <= port <= 65535:
            raise ConfigError(f"GARMIN_MCP_PORT={port} is outside 1-65535.")

        log_level = src.get("GARMIN_MCP_LOG_LEVEL", "INFO").strip().upper()
        if log_level not in _LOG_LEVELS:
            raise ConfigError(
                f"GARMIN_MCP_LOG_LEVEL={log_level!r} is not valid. "
                f"Use one of: {', '.join(_LOG_LEVELS)}."
            )

        auth_token = (src.get("GARMIN_MCP_AUTH_TOKEN") or "").strip() or None

        # Over HTTP this process is an unauthenticated proxy to personal health
        # data, so refuse to start rather than let a missing variable quietly
        # expose it. stdio is inherently scoped to whoever spawned the process.
        if transport == HTTP and auth_token is None:
            raise ConfigError(
                "Refusing to start: GARMIN_MCP_TRANSPORT=streamable-http exposes your "
                "Garmin data over the network, but GARMIN_MCP_AUTH_TOKEN is not set. "
                "Set it to a long random secret and have clients send "
                "'Authorization: Bearer <secret>'."
            )

        return cls(
            transport=transport,  # type: ignore[arg-type]
            host=src.get("GARMIN_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=port,
            token_store=(
                src.get("GARMINTOKENS", "").strip() or DEFAULT_TOKEN_STORE
            ),
            auth_token=auth_token,
            log_level=log_level,
        )
