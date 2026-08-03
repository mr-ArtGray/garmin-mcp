from __future__ import annotations

import pytest

from garmin_mcp.config import DEFAULT_TOKEN_STORE, ConfigError, Settings


def test_defaults_are_local_stdio() -> None:
    s = Settings.from_env(env={})
    assert s.transport == "stdio"
    assert s.is_http is False
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.token_store == DEFAULT_TOKEN_STORE
    assert s.auth_token is None


def test_http_requires_an_auth_token() -> None:
    """A missing variable must not silently expose health data to the network."""
    with pytest.raises(ConfigError) as excinfo:
        Settings.from_env(env={"GARMIN_MCP_TRANSPORT": "streamable-http"})

    message = str(excinfo.value)
    assert "GARMIN_MCP_AUTH_TOKEN" in message
    assert "Refusing to start" in message


def test_http_with_token_is_accepted() -> None:
    s = Settings.from_env(
        env={
            "GARMIN_MCP_TRANSPORT": "streamable-http",
            "GARMIN_MCP_AUTH_TOKEN": "secret",
            "GARMIN_MCP_HOST": "0.0.0.0",
            "GARMIN_MCP_PORT": "9000",
        }
    )
    assert s.is_http is True
    assert (s.host, s.port, s.auth_token) == ("0.0.0.0", 9000, "secret")


def test_stdio_does_not_require_a_token() -> None:
    """stdio is already scoped to whoever spawned the process."""
    assert Settings.from_env(env={"GARMIN_MCP_TRANSPORT": "stdio"}).auth_token is None


def test_blank_auth_token_counts_as_missing() -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(
            env={
                "GARMIN_MCP_TRANSPORT": "streamable-http",
                "GARMIN_MCP_AUTH_TOKEN": "   ",
            }
        )


@pytest.mark.parametrize(
    "env",
    [
        {"GARMIN_MCP_TRANSPORT": "sse"},
        {"GARMIN_MCP_TRANSPORT": "http"},
        {"GARMIN_MCP_PORT": "not-a-number"},
        {"GARMIN_MCP_PORT": "0"},
        {"GARMIN_MCP_PORT": "70000"},
        {"GARMIN_MCP_LOG_LEVEL": "VERBOSE"},
    ],
)
def test_invalid_values_are_rejected_with_guidance(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(env=env)


def test_token_store_is_overridable_for_container_mounts() -> None:
    s = Settings.from_env(env={"GARMINTOKENS": "/run/secrets/garmin"})
    assert s.token_store == "/run/secrets/garmin"
