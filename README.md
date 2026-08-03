# garmin-mcp

An MCP server that gives an LLM read-only access to your own Garmin Connect health data — sleep, HRV, body battery, stress, training readiness, activities and more.

Runs locally over stdio; switches to authenticated HTTP for remote use with environment variables only.

> **Heads up.** Garmin's official Health API is partner-only and rejects personal-use applications, so this uses Garmin's private Connect API via [`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect). That's against Garmin Connect's terms of service and can break without notice — it did in March 2026, when Garmin tightened its bot protection. Fine for personal use; don't build anything load-bearing on it.

## Requirements

- Python 3.12+ (`garminconnect` requires it — `uv` handles this for you)
- [uv](https://docs.astral.sh/uv/)
- A Garmin Connect account

## Setup

```bash
uv sync
uv run garmin-mcp-login
```

> **Run the login from a normal terminal, not an elevated/Administrator one.** On Windows an elevated process writes the token file with permissions that exclude your ordinary user account. Login appears to succeed, then every client that isn't elevated fails with `Access is denied` — see [Troubleshooting](#troubleshooting). The command warns and asks for confirmation if it detects this.

`garmin-mcp-login` asks for your email, password and MFA code once, then writes session tokens to `~/.garminconnect/`. Your password is used to obtain those tokens and is never stored.

This is a separate command for a reason: Garmin logins can require an MFA code, and an MCP server talking JSON-RPC over a pipe has nowhere to prompt. The server only ever reads the token file, and `garminconnect` refreshes the tokens on its own — you shouldn't need to log in again unless the refresh token expires or you revoke access.

Re-running the command is safe; it checks the existing tokens first and exits without asking for anything if they still work. **Don't re-run it speculatively when something breaks** — Garmin rate-limits login attempts per IP, and repeated tries make it worse. Almost every failure has a cause other than a dead token; check the log first.

## Use it with Claude Code

Copy `.mcp.json.example` to `.mcp.json` and set the absolute path to your checkout (`.mcp.json` is gitignored, since it is machine-specific). Or register it globally:

```bash
claude mcp add garmin -- uv --directory /path/to/garmin-mcp run garmin-mcp
```

## Use it with Claude Desktop

Add it to `claude_desktop_config.json` — on Windows `%APPDATA%\Claude\`, on macOS `~/Library/Application Support/Claude/`:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "/absolute/path/to/uv",
      "args": ["--directory", "/absolute/path/to/garmin-mcp", "run", "garmin-mcp"],
      "env": { "GARMINTOKENS": "/absolute/path/to/home/.garminconnect" }
    }
  }
}
```

Three things matter here, each of which will otherwise fail silently:

- **Use an absolute path to `uv`.** Claude Desktop does not inherit your shell `PATH`, so a bare `uv` is not found. `which uv` / `(Get-Command uv).Source` gives you the path.
- **Set `GARMINTOKENS` to an absolute path.** `~` resolves against the environment of whatever launched the process, which is not necessarily your shell's.
- **You do not run the server yourself.** Desktop spawns it on launch and kills it on quit; in stdio mode it has no port and nothing to attach to. After changing config, fully quit Desktop (tray icon → Quit) — closing the window leaves it running with the old config.

Then ask things like *"how did I sleep last night?"*, *"am I recovered enough to train hard today?"*, or *"how did my resting heart rate trend over the last month?"*

To check it by hand instead:

```bash
uv run mcp dev src/garmin_mcp/server.py     # MCP Inspector
```

## Tools

Date arguments are deliberately permissive, because a model writes dates the way a person would:

- keywords — `today`, `yesterday`, `last night`, `last week`, `last month`
- offsets — `-7d`, `7d`, `2 weeks ago`, `a month ago` (unsigned means the past; only `+3d` looks forward)
- absolute — `2026-08-02`, `2026/08/02`, `2026-08-02T22:15:00Z`

Sleep is recorded against the morning it ends, so last night's sleep is `date=today` (and `last night` maps there too).

A rejected date fails the call *before* any network request, which is invisible in the response — so unrecognised values are logged with their arguments rather than failing silently.

| Tool | What it returns |
|---|---|
| `garmin_get_health_snapshot` | Summary + sleep + HRV + body battery + readiness for one day, in one call |
| `garmin_get_daily_summary` | Steps, distance, floors, calories, intensity minutes, resting HR, stress |
| `garmin_get_sleep` | Sleep score and components, stage durations, overnight SpO2 and respiration |
| `garmin_get_hrv` | Overnight HRV, weekly average, baseline range, HRV status |
| `garmin_get_stress` | Average/max stress and the rest/low/medium/high split |
| `garmin_get_heart_rate` | Resting, min and max HR, seven-day resting average |
| `garmin_get_training_readiness` | Readiness score plus the factors behind it |
| `garmin_get_body_battery` | Daily high/low, charge and drain, over a range |
| `garmin_get_steps` | Daily step totals, goal and distance, over a range |
| `garmin_list_activities` | Recorded workouts in a range, with IDs |
| `garmin_get_activity` | Full detail for one workout by ID |
| `garmin_get_body_composition` | Weigh-ins: weight, BMI, body fat, muscle and bone mass |
| `garmin_get_user_profile` | Name, height, weight, and your measurement system |
| `garmin_api_request` | Any Garmin API path directly — the escape hatch |

Every tool is annotated `readOnlyHint: true`. Nothing here can modify your Garmin account.

### Why responses look trimmed

Garmin returns per-minute sample series; a single `get_sleep_data` call can exceed 100KB of JSON. By default those series are replaced with a marker like `{"_omitted": "480 samples omitted; call again with detail='full' to include them"}` and long lists are truncated to 8 entries.

Pass `detail="full"` when the individual data points actually matter.

## Adding more endpoints

`garminconnect` exposes ~140 methods; 13 are wired up above. Adding another is **one row** in `src/garmin_mcp/tools.py`:

```python
ToolSpec(
    name="garmin_get_spo2",
    method="get_spo2_data",          # any method on the garminconnect client
    title="Pulse oximetry",
    shape=Shape.DAILY,               # NONE | DAILY | RANGE
    description="Get overnight blood oxygen saturation for one day: ...",
),
```

Date parsing, payload trimming, read-only annotations, JSON serialisation and error handling all come from the registry (`src/garmin_mcp/registry.py`) — there is no function to write.

The three shapes map to how the underlying method takes arguments:

| Shape | Signature | Tool parameters |
|---|---|---|
| `Shape.NONE` | `method()` | `detail` |
| `Shape.DAILY` | `method(cdate)` | `date`, `detail` |
| `Shape.RANGE` | `method(startdate, enddate)` | `start_date`, `end_date`, `detail` |

Endpoints that don't fit — anything taking an ID, or fanning out across several calls — are written as ordinary `@mcp.tool()` functions in `server.py`; `garmin_get_activity` is the example to copy.

A test asserts every `method` in the table exists on the Garmin client, so a typo fails the suite rather than surfacing at call time.

**Explore before you commit to a row.** Use `garmin_api_request` to try an endpoint, and promote it once you find yourself reaching for it repeatedly.

## Running remotely

Nothing in the code is stdio-specific. Set the environment and it serves HTTP instead:

```bash
export GARMIN_MCP_TRANSPORT=streamable-http
export GARMIN_MCP_AUTH_TOKEN="$(openssl rand -hex 32)"
export GARMIN_MCP_HOST=0.0.0.0
uv run garmin-mcp
```

Clients then send `Authorization: Bearer <token>` to `http://host:8000/mcp`.

| Variable | Default | Purpose |
|---|---|---|
| `GARMIN_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `GARMIN_MCP_HOST` | `127.0.0.1` | HTTP bind address |
| `GARMIN_MCP_PORT` | `8000` | HTTP port |
| `GARMIN_MCP_AUTH_TOKEN` | — | **Required** for HTTP. Shared bearer secret |
| `GARMINTOKENS` | `~/.garminconnect` | Token store path — mount as a secret in a container |
| `GARMIN_MCP_LOG_LEVEL` | `INFO` | Log verbosity (stderr) |

Notes for when you deploy:

- **The server refuses to start over HTTP without `GARMIN_MCP_AUTH_TOKEN`.** Otherwise a missing variable would quietly publish your health data. The token is compared in constant time.
- HTTP runs stateless with plain JSON responses, so there's no session affinity to preserve — restart or replicate freely.
- Run `garmin-mcp-login` somewhere interactive and ship the resulting token file as a mounted secret; the server never needs your password.
- Terminate TLS in front of it. The bearer token is a shared secret and plain HTTP would leak it.
- For multi-user or proper OAuth, the SDK's `AuthSettings` / `token_verifier` hooks are untouched and additive — swap out `_BearerAuth` in `server.py`.

## Development

```bash
uv run pytest
```

108 tests, fully offline — no credentials or network required. They cover date parsing, payload trimming, config validation, all three registry shapes, tool registration, the health-snapshot fan-out, and the HTTP bearer guard.

## Diagnosing problems

**Read the log before changing anything.** The server writes every failure to stderr, including the tool name, the exception, and the arguments that caused it. MCP clients don't surface arguments anywhere, so this log is usually the only place the real cause appears.

Claude Desktop captures it per server:

```powershell
# Windows
Get-Content "$env:APPDATA\Claude\logs\mcp-server-garmin.log" -Tail 30 -Wait
```
```bash
# macOS
tail -f ~/Library/Application\ Support/Claude/logs/mcp-server-garmin.log
```

`mcp.log` alongside it covers connection-level problems. Under HTTP transport the same output goes to your terminal instead.

Two signals worth knowing:

- **A tool that fails in a few milliseconds never reached Garmin.** A real API call takes 100ms+; anything faster failed during argument parsing or before authentication.
- **`Garmin session established` appears in the log on every successful login.** Its absence means authentication never succeeded, regardless of what the error says.

## Troubleshooting

**"Access is denied" / "not permitted to read it"** — the token store was written by an **elevated (Administrator) terminal**, so its permissions exclude your normal user account. It then works from that terminal and fails everywhere else, which makes it look intermittent. Re-authenticating does *not* fix this — it would rewrite a file the client still can't read. Grant your account access instead, which keeps your existing token:

```powershell
icacls "$env:USERPROFILE\.garminconnect" /grant "$env:USERNAME:(OI)(CI)F" /T
```

Then run `garmin-mcp-login` from a **non-elevated** terminal in future.

**"session could not be refreshed just now"** — the token is intact and this is usually Garmin rate-limiting the connection. Wait a minute; the server also retries once automatically. Don't re-authenticate unless it persists.

**"no token store"** — run `uv run garmin-mcp-login`.

**Any authentication error, generally** — `garminconnect` reports an expired token, a rate-limited refresh, and an unreadable token file with the *same* message (`Username and password are required`), because it performs the refresh network call inside the same error handler as the token file read. This server classifies them and tells you which one it actually is. Trust that classification over the raw library message.

**Login fails mentioning Cloudflare or a 403** — Garmin likely changed its bot protection. Upgrade first: `uv sync --upgrade-package garminconnect`.

**"Garmin is rate limiting"** — wait a minute. Prefer one wide date range over many single-day calls.

**A date argument is rejected** — the parser accepts a wide range of forms, but not everything. The log line names the exact value it received.

**A metric comes back empty or errors inside `garmin_get_health_snapshot`** — usually means your device doesn't record it, or hasn't synced. The snapshot deliberately reports per-metric errors instead of failing the whole call. Note that a night with no recorded sleep returns a valid response with null fields, which is data absence, not an error.
