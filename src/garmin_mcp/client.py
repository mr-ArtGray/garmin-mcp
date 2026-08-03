"""Authenticated Garmin session, shared across tools.

The server never handles credentials. It loads the token store written by
``garmin-mcp-login`` and lets ``garminconnect`` refresh those tokens on its own.
That split exists because Garmin logins can demand an MFA code, which a server
speaking JSON-RPC over a pipe has no way to prompt for.

``garminconnect`` is synchronous and network-bound, so calls are dispatched to a
worker thread -- otherwise a slow Garmin response would stall the whole event
loop, which matters as soon as this runs over HTTP with concurrent clients.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from enum import Enum, auto
from pathlib import Path
from typing import Any

import anyio.to_thread
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)
from mcp.server.mcpserver.exceptions import ToolError

from .config import Settings

__all__ = ["GarminSession", "LOGIN_HINT", "TokenStore"]

logger = logging.getLogger(__name__)

LOGIN_HINT = "Run `uv run garmin-mcp-login` in a terminal to authenticate."


class TokenStore(Enum):
    """Why the token store is or is not usable.

    Kept distinct because the remedies differ sharply: one needs a login, one
    needs a permission change, and one needs only patience.
    """

    OK = auto()
    MISSING = auto()
    UNREADABLE = auto()
    """Present, but this process lacks permission -- usually an elevation mismatch."""
    MALFORMED = auto()


class GarminSession:
    """Lazily authenticated Garmin client.

    Login is deferred to the first tool call rather than done at startup: an MCP
    server that exits during initialisation because a token expired is far harder
    to diagnose from a client than a tool that returns a message telling you to
    log in again.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Garmin | None = None
        self._lock = threading.Lock()

    def _token_store_status(self) -> TokenStore:
        """Classify the token store on disk.

        ``garminconnect`` collapses every one of these into the same
        "Username and password are required", but they call for completely
        different responses -- and telling someone to re-authenticate when the
        real problem is a file they cannot read sends them to a rate-limited
        login endpoint for nothing.
        """
        store = Path(self._settings.token_store).expanduser()
        candidate = store / "garmin_tokens.json" if store.is_dir() else store
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return TokenStore.MISSING
        except PermissionError as exc:
            # Typically the store was written by an elevated process, leaving an
            # ACL that excludes the ordinary user account -- so it reads fine
            # from an admin terminal and fails from a normally-launched app.
            logger.warning("token store unreadable at %s: %s", candidate, exc)
            return TokenStore.UNREADABLE
        except Exception as exc:  # noqa: BLE001 - unparseable or otherwise broken
            logger.warning(
                "token store unusable at %s: %s: %s",
                candidate,
                type(exc).__name__,
                exc,
            )
            return TokenStore.MALFORMED

        return TokenStore.OK if data.get("di_refresh_token") else TokenStore.MALFORMED

    def _auth_message(self, exc: Exception) -> str:
        resolved = Path(self._settings.token_store).expanduser()
        status = self._token_store_status()

        if status is TokenStore.MISSING:
            return (
                f"Garmin authentication failed -- no token store at '{resolved}'. "
                f"{LOGIN_HINT} ({exc})"
            )

        if status is TokenStore.UNREADABLE:
            # Re-authenticating does not fix this and would just rewrite a file
            # this process still could not read, so say what actually helps.
            return (
                f"Garmin token store at '{resolved}' exists but this process is not "
                f"permitted to read it -- your login is fine, the file permissions "
                f"are not. This happens when `garmin-mcp-login` is run from an "
                f"elevated/Administrator terminal: the file it writes excludes your "
                f"normal user account, so it works from that terminal and fails "
                f"everywhere else. Fix without re-authenticating by granting your "
                f"account access:\n"
                f'  icacls "{resolved}" /grant "%USERNAME%:(OI)(CI)F" /T\n'
                f"({exc})"
            )

        if status is TokenStore.MALFORMED:
            return (
                f"Garmin token store at '{resolved}' is present but not valid -- it is "
                f"missing a refresh token or is not readable JSON. {LOGIN_HINT} ({exc})"
            )

        # Tokens are on disk and intact, so this is a refresh that did not land.
        # garminconnect performs the refresh network call inside the same try
        # block as the file load, so a 429 or a dropped connection surfaces as
        # "Username and password are required" -- indistinguishable from having
        # never logged in, and misleading enough to send you re-authenticating
        # when the stored token is still valid for hours.
        return (
            f"Garmin session could not be refreshed just now, but the stored token at "
            f"'{resolved}' is still present and intact -- this is usually Garmin rate "
            f"limiting the connection, not an expired login. Wait a minute and retry. "
            f"Only run `garmin-mcp-login` if it keeps failing, since repeated login "
            f"attempts make rate limiting worse. ({exc})"
        )

    def _connect(self) -> Garmin:
        """Authenticate from the token store. Runs on a worker thread."""
        with self._lock:
            if self._client is not None:
                return self._client

            last_exc: Exception | None = None
            # One quiet retry: the observed failure mode is a transient refresh,
            # and retrying beats surfacing an error the caller can only respond
            # to by retrying anyway. Deliberately not more than one -- extra
            # attempts feed the rate limiting that causes this.
            for attempt in range(2):
                client = Garmin()
                try:
                    client.login(self._settings.token_store)
                except Exception as exc:  # noqa: BLE001 - classified below
                    last_exc = exc
                    # Only a transient refresh is worth retrying; a missing,
                    # unreadable or malformed store fails identically forever.
                    if self._token_store_status() is not TokenStore.OK:
                        break
                    if attempt == 0:
                        logger.warning("Garmin login failed (%s); retrying once", exc)
                        time.sleep(2)
                    continue

                logger.info("Garmin session established")
                self._client = client
                return client

            assert last_exc is not None
            raise ToolError(self._auth_message(last_exc)) from last_exc

    def reset(self) -> None:
        """Drop the cached client so the next call re-authenticates."""
        with self._lock:
            self._client = None

    async def call(self, method: str, /, *args: Any, **kwargs: Any) -> Any:
        """Invoke a ``Garmin`` method off-thread, translating failures.

        Error messages name the next action, because the caller is a model that
        can act on "log in again" or "wait and retry" but not on a stack trace.
        """

        def _invoke() -> Any:
            client = self._connect()
            fn = getattr(client, method, None)
            if not callable(fn):
                raise ToolError(
                    f"'{method}' is not available on this version of the Garmin client."
                )
            return fn(*args, **kwargs)

        try:
            return await anyio.to_thread.run_sync(_invoke)
        except ToolError:
            raise
        except GarminConnectAuthenticationError as exc:
            # Tokens were accepted at connect time but rejected since. Clear the
            # cached client so a later call can recover without a restart, and
            # classify rather than assuming the login is dead.
            self.reset()
            raise ToolError(self._auth_message(exc)) from exc
        except GarminConnectTooManyRequestsError as exc:
            raise ToolError(
                "Garmin is rate limiting this account. Wait about a minute before "
                f"retrying, and prefer wider date ranges over many single-day calls. ({exc})"
            ) from exc
        except GarminConnectNotFoundError as exc:
            raise ToolError(
                f"Garmin has no data at that location -- check the date or ID. ({exc})"
            ) from exc
        except GarminConnectConnectionError as exc:
            raise ToolError(f"Could not reach Garmin Connect: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - last resort, keep the message useful
            raise ToolError(f"Garmin request '{method}' failed: {exc}") from exc
