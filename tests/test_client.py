"""Auth failure classification.

`garminconnect` reports "never logged in" and "refresh did not land just now"
identically, because it performs the refresh network call inside the same try
block as the token file load -- so a 429 surfaces as "Username and password are
required". Telling a user to re-authenticate when their token is still valid for
hours sends them to hammer an already rate-limited endpoint, so these two cases
must not be conflated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from garminconnect import GarminConnectAuthenticationError
from mcp.server.mcpserver.exceptions import ToolError

from garmin_mcp.client import GarminSession, TokenStore
from garmin_mcp.config import Settings

pytestmark = pytest.mark.anyio


def make_session(tmp_path: Path, tokens: dict | str | None) -> GarminSession:
    store = tmp_path / ".garminconnect"
    store.mkdir()
    if tokens is not None:
        body = tokens if isinstance(tokens, str) else json.dumps(tokens)
        (store / "garmin_tokens.json").write_text(body)
    return GarminSession(Settings.from_env(env={"GARMINTOKENS": str(store)}))


VALID = {"di_token": "a.b.c", "di_refresh_token": "refresh", "di_client_id": "x"}


def test_intact_tokens_are_reported_as_a_transient_refresh(tmp_path: Path) -> None:
    session = make_session(tmp_path, VALID)
    message = session._auth_message(
        GarminConnectAuthenticationError("Username and password are required")
    )

    assert "rate limiting" in message
    assert "Wait a minute and retry" in message
    # Must not tell the user to re-authenticate as the primary action.
    assert "still present and intact" in message


def test_missing_tokens_are_reported_as_needing_login(tmp_path: Path) -> None:
    session = make_session(tmp_path, None)
    message = session._auth_message(GarminConnectAuthenticationError("nope"))

    assert "no token store" in message
    assert "garmin-mcp-login" in message


@pytest.mark.parametrize(
    "tokens",
    [
        "not json at all",
        {"di_token": "a.b.c"},  # no refresh token -> cannot recover unattended
        {},
    ],
)
def test_unusable_token_files_are_reported_as_malformed(
    tmp_path: Path, tokens: dict | str
) -> None:
    session = make_session(tmp_path, tokens)
    assert session._token_store_status() is TokenStore.MALFORMED
    assert "not valid" in session._auth_message(RuntimeError("x"))


def test_intact_token_file_is_detected(tmp_path: Path) -> None:
    assert make_session(tmp_path, VALID)._token_store_status() is TokenStore.OK


def test_unreadable_store_is_not_reported_as_a_login_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An elevation/ACL mismatch reads as 'Access is denied'. Re-authenticating
    cannot fix it -- it would rewrite a file this process still cannot read."""
    session = make_session(tmp_path, VALID)

    def denied(*args, **kwargs):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(Path, "read_text", denied)

    assert session._token_store_status() is TokenStore.UNREADABLE
    message = session._auth_message(
        GarminConnectAuthenticationError("Username and password are required")
    )
    assert "not permitted to read it" in message
    assert "your login is fine" in message
    assert "icacls" in message
    # The remedy is a permission change, so a login prompt must not lead.
    assert message.index("not permitted") < message.index("garmin-mcp-login")


async def test_transient_failure_is_retried_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The observed failure recovered on its own; retrying beats surfacing an
    error whose only remedy is to retry."""
    session = make_session(tmp_path, VALID)
    attempts: list[int] = []

    class FlakyGarmin:
        def __init__(self) -> None:
            self.full_name = "Test User"

        def login(self, store: str) -> None:
            attempts.append(1)
            if len(attempts) == 1:
                raise GarminConnectAuthenticationError(
                    "Username and password are required"
                )

        def get_thing(self) -> dict:
            return {"ok": True}

    monkeypatch.setattr("garmin_mcp.client.Garmin", FlakyGarmin)
    monkeypatch.setattr("garmin_mcp.client.time.sleep", lambda _: None)

    assert await session.call("get_thing") == {"ok": True}
    assert len(attempts) == 2


async def test_missing_tokens_are_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrying an absent token store cannot succeed and only adds load."""
    session = make_session(tmp_path, None)
    attempts: list[int] = []

    class NoTokens:
        def login(self, store: str) -> None:
            attempts.append(1)
            raise GarminConnectAuthenticationError("Username and password are required")

    monkeypatch.setattr("garmin_mcp.client.Garmin", NoTokens)

    with pytest.raises(ToolError, match="no token store"):
        await session.call("get_thing")
    assert len(attempts) == 1


async def test_unreadable_store_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permission failure is permanent until fixed; retrying only adds delay."""
    session = make_session(tmp_path, VALID)
    attempts: list[int] = []

    class Denied:
        def login(self, store: str) -> None:
            attempts.append(1)
            raise GarminConnectAuthenticationError("Username and password are required")

    def denied(*args, **kwargs):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr("garmin_mcp.client.Garmin", Denied)
    monkeypatch.setattr(Path, "read_text", denied)

    with pytest.raises(ToolError, match="not permitted to read it"):
        await session.call("get_thing")
    assert len(attempts) == 1


async def test_persistent_failure_with_tokens_still_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session(tmp_path, VALID)

    class AlwaysFails:
        def login(self, store: str) -> None:
            raise GarminConnectAuthenticationError("429 rate limited")

    monkeypatch.setattr("garmin_mcp.client.Garmin", AlwaysFails)
    monkeypatch.setattr("garmin_mcp.client.time.sleep", lambda _: None)

    with pytest.raises(ToolError, match="rate limiting"):
        await session.call("get_thing")
