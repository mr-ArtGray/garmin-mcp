"""One-time interactive login.

Separate from the server because Garmin may demand an MFA code, and an MCP
server speaking JSON-RPC over stdio has nowhere to prompt. This runs in a real
terminal, completes the challenge, and writes the token store the server then
uses on its own.

Credentials are used to obtain tokens and are never written to disk.
"""

from __future__ import annotations

import os
import sys
from getpass import getpass
from pathlib import Path

from garminconnect import Garmin, GarminConnectAuthenticationError

from .config import DEFAULT_TOKEN_STORE

__all__ = ["main"]


def _is_elevated() -> bool:
    """Whether this process is running as Administrator (Windows only)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - detection is advisory only
        return False


def _warn_if_elevated() -> None:
    """Refuse to quietly write a token store the server will not be able to read.

    An elevated terminal writes the token file with an ACL that excludes the
    ordinary user account. Login then appears to succeed, and every later call
    from a normally-launched client fails with a permission error that looks
    nothing like its cause -- so it is worth stopping for.
    """
    if not _is_elevated():
        return

    print(
        "WARNING: this terminal is running as Administrator.\n"
        "Tokens written now will be readable only by elevated processes, so\n"
        "Claude Desktop and other normally-launched clients will fail with\n"
        "'Access is denied' even though the login succeeded.\n",
        file=sys.stderr,
    )
    answer = input("Continue anyway? [y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        print(
            "Aborted. Re-run this from a normal (non-Administrator) terminal.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _prompt_mfa() -> str:
    """Called by garminconnect only when Garmin challenges the login."""
    code = input("Garmin MFA code (check your email or authenticator app): ").strip()
    if not code:
        raise SystemExit("No MFA code entered; aborting.")
    return code


def _token_store() -> str:
    return os.environ.get("GARMINTOKENS", "").strip() or DEFAULT_TOKEN_STORE


def _already_valid(store: str) -> Garmin | None:
    """Return a working client if the existing token store still authenticates.

    Checked first so re-running this command is harmless and does not ask for a
    password that is not needed.
    """
    if not Path(store).expanduser().exists():
        return None
    client = Garmin()
    try:
        client.login(store)
    except Exception:  # noqa: BLE001 - any failure means "log in properly"
        return None
    return client


def main() -> None:
    store = _token_store()
    resolved = Path(store).expanduser()

    existing = _already_valid(store)
    if existing is not None:
        name = existing.full_name or existing.display_name or "your account"
        print(f"Already authenticated as {name}.")
        print(f"Tokens: {resolved}")
        print("Delete that directory and re-run this command to switch accounts.")
        return

    _warn_if_elevated()

    print("Logging in to Garmin Connect.")
    print("Your password is used once to obtain tokens and is never stored.\n")

    email = os.environ.get("GARMIN_EMAIL", "").strip() or input("Garmin email: ").strip()
    password = os.environ.get("GARMIN_PASSWORD", "") or getpass("Garmin password: ")

    if not email or not password:
        print("Email and password are both required.", file=sys.stderr)
        raise SystemExit(2)

    client = Garmin(email, password, prompt_mfa=_prompt_mfa)
    try:
        client.login(store)
    except GarminConnectAuthenticationError as exc:
        print(f"\nGarmin rejected the login: {exc}", file=sys.stderr)
        print(
            "Check the email and password, and note that Garmin briefly locks "
            "accounts after repeated failures.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except Exception as exc:  # noqa: BLE001
        print(f"\nLogin failed: {exc}", file=sys.stderr)
        print(
            "If this mentions Cloudflare or a 403, Garmin may have changed its bot "
            "protection again -- upgrading garminconnect usually fixes it: "
            "`uv sync --upgrade-package garminconnect`",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    name = client.full_name or client.display_name or "your account"
    print(f"\nAuthenticated as {name}.")
    print(f"Tokens written to {resolved}")
    print("The MCP server reads these directly; you should not need to log in again")
    print("unless the refresh token expires or you revoke access.")


if __name__ == "__main__":
    main()
