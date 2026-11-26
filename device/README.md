# Server Module — PAI Device Agent

The device-side runtime for the Personal AI (PAI) system. Registers with the backend as a device, maintains a persistent WebSocket connection, installs and runs plugins on demand, and executes capabilities requested by the backend.

```
┌───────────────────┐         ┌────────────────────────┐
│  PAI Backend      │◄───WS──►│  server (this module)  │
│  (FastAPI)        │◄──HTTP──►│  Device Agent          │
└───────────────────┘         │  ~/.pai/state/         │
                              │  ~/.pai/plugins/       │
                              └────────────────────────┘
```

---

## Table of contents

- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Running the agent](#running-the-agent)
- [Command-line interface](#command-line-interface)
- [Plugin system](#plugin-system)
- [Development](#development)
- [Logging](#logging)
- [Troubleshooting](#troubleshooting)
- [Architecture notes](#architecture-notes)

---

## What it does

The agent is a long-running process that:

1. **Authenticates** with the backend using email + password (`POST /auth/login`), caching the JWT and refreshing it automatically on expiry or `401`.
2. **Registers** itself as a device (`POST /devices/register`), persisting the assigned `device_id` in `~/.pai/state/device_id` so restarts are idempotent.
3. **Maintains a WebSocket** to `/devices/{id}/ws?token=<jwt>` — the channel the backend uses to push installs, uninstalls, and capability invocations.
4. **Reconciles** state every 60 seconds by comparing:
   - the backend's `device_plugins` table (desired state)
   - what's actually on disk in `~/.pai/plugins/` (installed state)
   - what's actually running in memory (running state)

   Drift in version or artifact sha triggers an automatic reinstall.
5. **Installs plugins** on demand: downloads the artifact from the backend, verifies the sha256 against the registry index, installs pip dependencies, runs post-install scripts, and instantiates the plugin adapter.
6. **Executes capabilities** requested via WebSocket, returning the result to the backend's `device_executor`.

The `pai-device` command offers a terminal interface to the same backend, for inspecting catalogs, installing plugins, and invoking capabilities without touching the agent process.

---

## Quick start

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (for dependency management)
- A running PAI backend (see the parent repo's top-level README)

### Install

```bash
cd server
uv sync
```

This reads `.python-version`, creates `.venv`, resolves dependencies, and installs the `server` package in editable mode.

### Configure

Create `.env` at the `server/` root:

```env
PAI_BACKEND_URL=http://localhost:8000
PAI_EMAIL=agent-user@example.com
PAI_PASSWORD=strong-password-here

# Optional
PAI_DEVICE_NAME=worker-01          # defaults to hostname
PAI_DEVICE_TYPE=server             # server | desktop | mobile | tablet
```

If the backend account doesn't exist yet:

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"agent-user@example.com","password":"strong-password-here"}'
```

The first user in the system is automatically promoted to admin.

### Run

```bash
uv run --active --env-file .env server
```

Expected output:

```
INFO [pai_device.transport] Logging in as agent-user@example.com
INFO [pai_device.transport] Logged in as agent-user@example.com (admin=False)
INFO [server.agent] Registered as device b4686279-… (Device registered)
INFO [server.agent] WS handshake complete for b4686279-…
```

The agent is now online. Install a plugin:

```bash
uv run --active --env-file .env pai-device install browser --watch
```

---

## Configuration

Settings come from, in this order: **environment variables**, then a **YAML config file**, then built-in defaults.
The file is looked for at `--config FILE` / `$PAI_DEVICE_CONFIG` (must exist), `./pai-device.yaml`, then
`~/.pai/pai-device.yaml`. [`pai-device.yaml`](pai-device.yaml) in this folder is the template: it lists every key
with its meaning and the environment variable that overrides it. An unknown key is an error, so a typo can't
silently do nothing. `pai-device config show` prints the effective settings (the password is never shown).

```bash
cp pai-device.yaml ~/.pai/pai-device.yaml        # edit: backend.url, account.email, device.name ...
export PAI_SERVER_PASSWORD=...                   # keep the password out of the file
pai-server                                       # or: pai-server --config /path/to/pai-device.yaml
```

| YAML key | Environment variable | Default |
|---|---|---|
| `backend.url` | `PAI_BACKEND_URL` | required, e.g. `http://localhost:8000` |
| `account.email` | `PAI_SERVER_EMAIL` (alias `PAI_USERNAME`) | required |
| `account.password` | `PAI_SERVER_PASSWORD` | required (prefer the env var; if it is in a file, `chmod 600` it) |
| `device.name` / `device.type` | `PAI_DEVICE_NAME` / `PAI_DEVICE_TYPE` | hostname / `server` |
| `paths.home` | `PAI_HOME` | `~/.pai` |
| `paths.state_dir` / `plugin_dir` / `log_dir` | `PAI_STATE_DIR` / `PAI_PLUGIN_DIR` / `PAI_LOG_DIR` | `<home>/state`, `/plugins`, `/logs` |
| `timing.heartbeat_interval` / `reconcile_interval` | | `20` / `60` seconds |
| `timing.reconnect_min` / `reconnect_max` | | `1` / `60` seconds |
| `timing.progress_keepalive` | | `20` seconds (keep well under the backend's 120 s stall limit) |
| `remote_access.install` / `.invoke` | `PAI_REMOTE_INSTALL` / `PAI_REMOTE_INVOKE` | `false` / `false` |

`remote_access.*` are only the **starting** values: what other devices may do to this one is the owner's setting on
the backend, which the agent receives as soon as it connects (and on every change) and which then replaces them.
Both default to `false` (fail closed), so until that report arrives nothing remote is accepted. See
[Remote access](#remote-access-who-may-install--run-plugins-on-this-device).

### State and data locations

| Path | Contains | Deletable? |
|---|---|---|
| `~/.pai/state/device_id` | Persisted device id — reuse across restarts | Yes; new id will be minted on next start |
| `~/.pai/plugins/<id>/<version>/` | Installed plugin files, manifest, sha256 | Yes (agent re-downloads on next sync) |
| `~/.pai/plugins/_downloads/` | Transient download staging | Yes, always safe |
| `~/.cache/ms-playwright/` | Chromium binaries (Playwright plugins) | Yes; re-downloaded on next plugin install |
| `logs/` | Agent logs (relative to CWD) | Yes; rotated |

---

## Running the agent

### Foreground

```bash
uv run --active --env-file .env server
```

`Ctrl+C` triggers graceful shutdown: WS close, plugin unload, httpx close.

### Development mode (`--reload`)

```bash
uv add --dev watchfiles            # one-time
uv run --active --env-file .env server --reload
```

Spawns the agent as a child process and restarts it whenever any `.py` file under `src/server/` changes. Restart is ~2 s.

```
INFO [__main__] [reload] agent started (pid 48211), watching /…/src/server
# edit src/server/plugins.py
INFO [__main__] [reload] plugins.py — restarting
INFO [__main__] [reload] agent restarted (pid 48392)
```

**Note:** editing `.env` does **not** trigger a reload — it's consumed by `uv run` before `main()` runs. Restart the outer `uv run` command to pick up env changes.

### As a console script (installed)

```bash
uv run --active server                       # equivalent to `python -m server`
uv run --active server --reload
```

### With systemd (production)

```ini
# /etc/systemd/system/pai-agent.service
[Unit]
Description=PAI Device Agent
After=network-online.target

[Service]
Type=simple
User=pai
WorkingDirectory=/opt/pai/server
EnvironmentFile=/opt/pai/server/.env
ExecStart=/opt/pai/server/.venv/bin/server
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now pai-agent
sudo journalctl -u pai-agent -f
```

---

## Command-line interface

`pai-device` is the whole client in one command: talk to the assistant (text and voice), manage your plugins and
settings, run capabilities, or start the device agent. It reads the same environment as the agent
(`PAI_BACKEND_URL`, `PAI_SERVER_EMAIL`, `PAI_SERVER_PASSWORD`; `pai-device config show` prints the effective
settings with the password masked), logs in once and runs one command.

```bash
pai-device                 # no arguments, or `pai-device run`: start the agent (same as `pai-server`)
pai-device <command> --help
```

Exit codes: `0` ok, `1` failed (the message is the server's own words), `2` bad usage / missing setup,
`3` an action is waiting for your approval, `130` interrupted.

### Talk to the assistant

| Command | What it does |
|---|---|
| `chat "message"` | One message; the reply streams as it is written. Progress lines (`· Understanding your request…`) go to stderr, so `chat … > answer.txt` keeps only the answer. Text piped on stdin works too. |
| `chat` | Interactive session (`/new`, `/status`, `/help`, `/quit`). Ctrl+C stops the current reply, not the session. |
| `voice` | Realtime voice over `WS /voice/stream`: your microphone streams up while you talk, the spoken answer plays as it is generated. Needs `pip install 'pai-device[voice]'` (and PortAudio, e.g. `apt install libportaudio2`). |
| `voice --ptt` | Push-to-talk: Enter starts talking, Enter again sends. |
| `voice --text-only` | Type your messages, read (and hear, if a speaker exists) the answers. Works without the voice extra. |
| `voice --barge-in` | Keep the microphone open while the assistant speaks so you can interrupt it. Use headphones: a laptop speaker leaks into its microphone, so by default the microphone is muted while the assistant talks. |

`chat` and `voice` share one conversation (the last session id is kept in the state dir; `--new` starts fresh,
`--session ID` picks one). When an action needs your approval, `chat` asks right there (`y` once, `s` this session
if the server allows it, anything else denies); without a terminal it prints `pai-device approve <task>` and does
**not** approve anything.

### Settings and account

| Command | What it does |
|---|---|
| `settings [show]` / `settings set --name … --timezone … --language … --remote-install on\|off --remote-invoke on\|off` | Your profile plus this device's remote-access switches, in one place |
| `remote-access show` / `remote-access set --install on\|off --invoke on\|off` | Who may install / run plugins on this device **from outside it**. Both are **off by default**: until you switch them on, nothing but this device itself (your other devices, the web, schedules, agents) can install plugins here or run them here |
| `profile show` / `profile set --name --timezone --language` | Your profile (an empty value clears it) |
| `password` | Change your password (prompts without echo; never taken as an argument) |
| `devices` | Your devices, whether they are online, and their remote-access switches (`*` = this device) |
| `plugins [list]` | Plugins for your account, plus this device's install state |
| `plugins enable <id>` / `plugins disable <id>` | Switch a plugin on or off for your account |
| `config show` | The effective local configuration (password masked) |

### Install, run, approve

| Command | What it does |
|---|---|
| `install <id>[@ver] [--device D] [--force] [--no-wait] [--timeout N]` | Ask a device to install a plugin and **follow it live**: `request received → sent to the device → downloading → verifying → installing → done`, or a plain failure message with a hint and a non-zero exit. `--no-wait` only prints the operation id |
| `uninstall <id> [--device D] [--no-wait]` | Same, for removal |
| `operations [--device D] [--plugin P] [--active]` | Recent installs/removals and how they went |
| `invoke <capability> [--device D] [--param k=v …] [--json-params '{…}'] [--timeout N] [--approve] [--scope once\|session\|always]` | Run a capability through the real pipeline (authorization, confirmation, audit) and wait for the result |
| `approve <task> [--scope …]` / `deny <task>` | Answer a task that waits for your confirmation |
| `list`, `show <id>`, `status`, `installed`, `registry`, `catalog`, `system`, `enable`, `disable` | Catalog and per-device plugin state (unchanged) |

Acting on **another** device (`--device <other>`) is a remote action: the backend refuses it with the owner's own
message unless that device has the matching switch on (`remote-access set` on that device, or the Settings page).
Acting on this device from itself is always allowed. `install`/`uninstall` use short polling *inside the CLI only*
(a CLI has no WebSocket to be pushed to); the apps and this agent get the same updates pushed live.

`invoke` typed parameters: `--param n=3 --param ok=true --param name=bob` (values that parse as JSON are typed, the rest
are text); `--json-params '@file.json'` reads a file, and with no parameters at all JSON on stdin is used. An action that
needs confirmation is shown to you (capability, risk, parameters with secrets masked) and **does not run** unless you
pass `--approve` (or approve it in the app). `--scope session|always` is only honoured when the server allows it.

### Common options

| Option | Applies to | Notes |
|---|---|---|
| `--device <id>` | Most | Overrides `~/.pai/state/device_id` |
| `--json` | Most | Raw JSON output (pipe to `jq`) |

### Examples

```bash
pai-device chat "what's the weather like?"          # streams the answer
pai-device voice --ptt                               # push-to-talk (Enter to talk, Enter to send)
pai-device install browser_linux@1.0.1               # follows the install; exit 0 only if it worked
pai-device install camera_linux --device $PHONE      # needs the phone's "Allow remote plugin install" switch
pai-device remote-access set --invoke on             # let my other devices run plugins on THIS device
pai-device invoke files.read --param path=/etc/hostname
pai-device invoke shell.exec --param command="uname -a" --approve   # you have seen what it will do
pai-device operations --active
pai-device list --json | jq '.plugins[].id'
```

### Shell completion (optional)

```bash
pip install argcomplete
eval "$(register-python-argcomplete pai-device)"      # add to ~/.bashrc
```

---

## Running in Docker

```bash
docker build -f docker/Dockerfile.device -t pai-device .
PAI_SERVER_EMAIL=you@example.com PAI_SERVER_PASSWORD=... ./scripts/run_pai_server_device_docker.sh
```

* Everything the agent stores (device id, downloaded plugins, logs) lives under `$PAI_HOME`
  (`/home/pai/.pai` in the image; override the pieces with `PAI_STATE_DIR` / `PAI_PLUGIN_DIR`). Mount a
  volume there to keep it across runs.
* **"Permission denied: /home/pai/.pai/state"** means that volume is owned by root (created by an older
  image). Remove it once with `docker volume rm pai-device`, or run as your own user with a folder you own:
  `--user "$(id -u):$(id -g)" -e PAI_HOME=/data -v "$HOME/.pai-device:/data"`. The agent checks the
  directories at start and prints these hints instead of a traceback.
* Inside a container `localhost` is the container: use `--network host` (Linux) or the host's address for
  `PAI_BACKEND_URL`.
* A container only sees what you give it. Plugins that need the machine itself (camera, screenshots,
  keyboard/mouse, audio) need the host devices passed in, for example `--device /dev/video0 --group-add video`
  or the X11/Wayland and PulseAudio sockets; without them those plugins report a permission error. A
  headless server container runs the Python server plugins only.

## Remote access (who may install / run plugins on this device)

By default nothing outside this device can change it. Other devices of the same user, the web, schedules
and agents can install plugins on it, or run plugins on it, only after the owner turns on **Allow remote
plugin install** / **Allow remote plugin use** for this device (the PAI app's settings, or the account
Settings page at `/settings` on the backend).

* The agent learns the switches from the backend (`connected` frame, then `device.remote_access` frames when the
  owner changes them) and **fails closed**: until told, remote requests are refused.
* It enforces them itself, in addition to the backend: a `plugin.install`/`plugin.uninstall` whose
  `requested_by_device` is not this device, or an invoke whose `source_device_id` is not this device, is refused
  while the matching switch is off. Your own CLI commands on this device are local and always allowed.
* Every API call carries `X-PAI-Device-Id` so the backend can tell "this device acting on itself" from remote.
* While installing, the agent sends `plugin.progress` frames (`downloading`, `verifying`, `installing`) so the
  requesting app shows live status instead of polling.

## Plugin system

Plugins extend what the device can do. The backend holds the **catalog**; the device holds the **installed** plugins; the agent reconciles the two.

### Plugin artifacts

Two artifact types are supported, distinguished by file extension:

| Extension | Adapter | Entry point contract |
|---|---|---|
| `.py` | `PythonPluginAdapter` | `create_plugin(config) -> Plugin` |
| `.so` / `.dylib` / `.dll` | `NativePluginAdapter` | `pai_plugin_init` / `pai_plugin_invoke` / … |

### Python plugin contract

The artifact is a single `.py` file exposing a module-level `create_plugin(config)` function:

```python
"""camera plugin — capture still frames."""
from __future__ import annotations
from typing import Any, Dict


class CameraPlugin:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config or {}

    async def invoke(self, capability: str, parameters: Dict[str, Any]) -> Any:
        if capability == "camera.capture":
            return await self._capture(parameters)
        raise ValueError(f"unknown capability: {capability}")

    async def shutdown(self) -> None:
        pass  # release resources

    async def _capture(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ...


def create_plugin(config: Dict[str, Any]) -> CameraPlugin:
    return CameraPlugin(config)
```

- `create_plugin` may be `async def` (adapter awaits the result). Useful for one-time setup (launch browser, open DB, load model).
- `invoke` may be sync or async — the adapter awaits if it returns an awaitable.
- `shutdown` is optional. Called on unload.

### Manifest

Each plugin ships a `manifest.json` in the backend's `plugin_registry/<id>/<version>/`:

```json
{
  "id": "browser",
  "name": "Browser",
  "version": "1.0.1",
  "description": "Web automation via Playwright.",
  "runtime": { "kind": "device", "nativeModule": "pai.browser.server" },
  "platforms": {
    "linux": { "artifact": "server/browser_plugin.py" }
  },
  "architectures": ["x86_64"],
  "device_types": ["server"],

  "dependencies": ["playwright>=1.48,<2.0", "httpx>=0.27,<1.0"],
  "post_install": [
    ["python", "-m", "playwright", "install", "chromium"]
  ],

  "capabilities": [
    {
      "id": "browser.navigate",
      "description": "Open a URL and return the rendered page.",
      "parameters": {
        "type": "object",
        "properties": { "url": { "type": "string" } },
        "required": ["url"]
      }
    }
  ]
}
```

| Field | Purpose |
|---|---|
| `platforms` | Maps platform → artifact path (relative to version dir) |
| `architectures` | Supported CPU architectures |
| `device_types` | Which device classes can install it (`server`, `desktop`, …) |
| `dependencies` | pip packages installed into the agent's venv on first start |
| `post_install` | argv lists run after `pip install` (e.g. Playwright's Chromium download) |
| `capabilities` | What `invoke()` accepts, with JSON Schema parameters |

### Dependency installation

Deps are installed **once per (plugin, deps-hash)**, into the agent's own venv. The agent uses `uv pip install --python <sys.executable>` if `uv` is on `PATH`, falling back to `python -m pip install` otherwise. A `.deps-installed` marker in the plugin directory stores the hash of `{"deps": [...], "post": [...]}` — changing the manifest invalidates the marker automatically.

**Concurrency caveat:** all plugins share the agent's venv. Two plugins with overlapping dependency ranges can fight — plugin A wants `httpx<0.28`, plugin B wants `httpx>=0.28`; the second install silently breaks the first. Keep dependency ranges non-overlapping, or wait for per-plugin venv isolation (not yet implemented).

### Lifecycle

```
install:
  download artifact → sha256 verify → plugins.install()
    → stop existing adapter (if any)
    → copy files to ~/.pai/plugins/<id>/<version>/
    → write manifest.json, sha256.txt
    → start(): pip install deps → post_install → load module → create_plugin()

disable:
  plugins.stop() → adapter.shutdown(), keep files

uninstall:
  plugins.uninstall() → adapter.shutdown() → rmtree(<id>/)

restart agent:
  plugins.load_existing() → register all on-disk plugins (NOT started)
                          → _sync_loop decides what runs
```

The `load_existing` step deliberately **does not start** plugins. Startup policy lives in the backend's `device_plugins` table; `_sync_loop` reads it 2 s after registration and starts only what the user has enabled.

### Version drift and sha drift

`_sync_once` (every 60 s) compares:

| Local state | Backend state | Action |
|---|---|---|
| installed v1.0.0 | target v1.0.0, same sha | start if stopped |
| installed v1.0.0 | target v1.0.1 | reinstall v1.0.1 |
| installed v1.0.0 | target v1.0.0, **different sha** | reinstall v1.0.0 (catches in-place edits) |
| disabled | running | stop |
| absent | target v1.0.0 | install |
| present | not in backend | uninstall |

Sha-drift detection makes **in-place edits** work: change `plugin_registry/browser/1.0.1/server/browser_plugin.py` on the backend without bumping the version, and the agent will re-download and reinstall it within 60 s. Deps are skipped (the hash didn't change); only the `.py` file is replaced.

For a real release, bump the version so the old one stays on disk for rollback:

```bash
python scripts/bump_plugin.py browser patch --reload-backend
pai-device install browser@<new-version> --watch
```

### Versioning

`scripts/bump_plugin.py` coordinates the two places version lives — the directory name and `manifest.version`:

```bash
python scripts/bump_plugin.py --list                        # show current versions
python scripts/bump_plugin.py browser patch --dry-run       # preview
python scripts/bump_plugin.py browser patch --commit        # bump + git commit
python scripts/bump_plugin.py browser minor --reload-backend
```

The old version stays on disk — `pai-device install browser@1.0.0 --watch` rolls back without rebuilding.

---

## Development

### Project layout

```
server/
  pyproject.toml
  .env                       # not committed
  src/server/
    __init__.py              # exports main, DeviceAgent, DeviceConfig
    __main__.py              # 3-line shim for `python -m server`
    _entry.py                # main() body: argparse, --reload, logging
    agent.py                 # DeviceAgent: WS loop, sync, dispatch
    config.py                # DeviceConfig + wire models (pydantic)
    transport.py             # APIClient (login/retry) + WSClient
    plugins.py               # DevicePluginManager, adapters
    cli.py                   # pai-device
  scripts/
    bump_plugin.py           # versioning helper
    test_server.py           # HTTP fixture server for tests
    test_browser_plugin.sh   # E2E test suite
```

### Common tasks

| Task | Command |
|---|---|
| Add a runtime dependency | `uv add httpx` |
| Add a dev dependency | `uv add --dev pytest ruff` |
| Run a one-off tool | `uv run --active --with ipython ipython` |
| Refresh lockfile | `uv lock` (or `uv lock --upgrade`) |
| Build sdist + wheel | `uv build` → `dist/` |
| Fresh venv | `rm -rf .venv && uv sync` |

### Testing the browser plugin

Requires the agent running with `browser` installed and enabled:

```bash
# terminal 1
uv run --active --env-file .env server --reload

# terminal 2
./scripts/test_browser_plugin.sh
```

Asserts on `browser.navigate`, `browser.fill_form`, `browser.book`, error paths, and the three params-passing mechanisms (inline, stdin, `@file`). Exit 0 on success, 1 on failure.

### Debugging

```bash
# Watch plugin events only
tail -f logs/app.log | grep '\[server.plugins\]'

# Watch HTTP traffic
tail -f logs/http_access.log

# Verbose HTTP in terminal
uv run --active --env-file .env server --verbose-http

# Inspect installed plugin state
ls ~/.pai/plugins/
cat ~/.pai/plugins/browser/1.0.1/manifest.json
cat ~/.pai/plugins/browser/1.0.1/sha256.txt
cat ~/.pai/plugins/browser/1.0.1/.deps-installed
```

---

## Logging

The agent splits logs across three sinks to keep the terminal readable:

| Sink | Contains | Format |
|---|---|---|
| Terminal (stderr) | Application events | Colorized, human-readable |
| `logs/app.log` | Same events, persisted | Plain text, rotated at 20 MB, gzipped |
| `logs/http_access.log` | Backend HTTP requests | One line per request |

`httpx` and `httpcore` are routed to `logs/http_access.log` with `propagate = False` — they never reach the terminal. Everything under `server.*` goes to the terminal and `app.log`.

### Flags

```bash
uv run --active --env-file .env server \
  --log-dir logs \           # default: ./logs
  --verbose-http             # also print httpx logs to the terminal
```

### Rotation

| File | Rolls at | Kept |
|---|---|---|
| `app.log` | 20 MB | 10 rotations (`.1.gz` … `.10.gz`) |
| `http_access.log` | 20 MB | 10 rotations |

Total footprint: ~200 MB per file. Adjust `_configure_logging()` in `src/server/_entry.py` if you need more or less.

### Reading it live

```bash
# Application events
tail -f logs/app.log

# Request traffic
tail -f logs/http_access.log

# Both, filtered to notable events
tail -f logs/app.log logs/http_access.log | grep -E 'ERROR|WARNING|Plugin|drift'
```

`lnav` parses both formats automatically and colorizes by level.

---

## Troubleshooting

### `config error: Missing required environment variable(s)`

`.env` doesn't have `PAI_BACKEND_URL`, `PAI_EMAIL`, or `PAI_PASSWORD`. Verify with:

```bash
uv run --active --env-file .env python -c "from server.config import DeviceConfig; print(DeviceConfig.from_env())"
```

### `Login failed (401): …`

The email/password in `.env` doesn't match a user. Register first:

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"...","password":"..."}'
```

### `WS auth rejected (4401)`

The JWT expired and re-login failed. Check the "Re-login failed:" line that follows in the log — usually bad credentials or the backend is unreachable.

### `WS ownership rejected (4003)`

The device belongs to a different user. The agent stops (retrying won't help). To reassign:

1. Delete `~/.pai/state/device_id` (forces a new id)
2. Restart the agent

The old device row stays in the backend's `devices` table; `pai-device status --device <old>` will show it offline.

### `HTTP 409: Device is not connected`

`pai-device install` and `uninstall` push over WS — they need the agent running. Either start it, or use `pai-device enable` (durable intent that takes effect on next sync).

### `No artifact for browser@1.0.1 installable on platform=…`

The manifest doesn't declare an artifact for this device's platform/architecture/device_type. Check:

```bash
pai-device show browser | jq '{platforms, architectures, device_types}'
```

### `pip install failed (1): No module named pip`

uv-created venvs have no `pip`. The agent prefers `uv pip install --python <sys.executable>` when `uv` is on `PATH`. If you see this error, `uv` isn't installed:

```bash
which uv          # should print a path
# if not:
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### `sudo: no tty present` during Playwright install

`playwright install chromium --with-deps` invokes `sudo apt-get` internally. In a headless deployment (systemd, Docker), that hangs. Pre-install the OS libs and drop `--with-deps`:

```bash
sudo apt install -y libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
  libgbm1 libpango-1.0-0 libcairo2 libasound2t64
```

```json
"post_install": [["python", "-m", "playwright", "install", "chromium"]]
```

### `RuntimeError: … must expose create_plugin(config)`

The Python plugin module doesn't have a `create_plugin` function at module scope. `class Plugin(config)` is not accepted — the adapter requires the factory form. Add:

```python
def create_plugin(config):
    return MyPlugin(config)
```

### Plugin won't update after editing the file on the backend

Two paths to force it:

```bash
# Fast (agent must be online)
pai-device install browser@<version> --force --watch

# Slow (agent offline; happens within 60 s anyway)
# just wait — _sync_once detects sha drift
```

If nothing happens after 60 s, check the agent log for `Sync sha drift` or `Sync install`. If neither appears, `/plugins/index.json` isn't returning the new sha — verify the file exists at the manifest's declared path:

```bash
cat plugin_registry/browser/1.0.1/manifest.json | jq '.platforms'
ls plugin_registry/browser/1.0.1/server/
```

### `RuntimeError: No plugin provides 'capability.x'`

The capability is not in the plugin's manifest, or the plugin isn't running. Check:

```bash
pai-device installed              # is_installed + enabled
pai-device show browser | jq '.capabilities'
```

### Agent keeps re-installing the same plugin every 60 s

Sha drift loop — the downloaded file's sha doesn't match the backend's. Usually means:

- A build artifact is being regenerated on every request (check the backend's `_sha256_file` caching)
- The manifest declares a path that resolves to a symlink to a temp file

Verify with:

```bash
# On the backend, sha of what's on disk
sha256sum plugin_registry/browser/1.0.1/server/browser_plugin.py

# On the device, sha the agent recorded
cat ~/.pai/plugins/browser/1.0.1/sha256.txt
```

They should match. If they differ, the backend is serving a different file than what the manifest points at.

---

## Architecture notes

### Why WebSocket + HTTP + polling

The agent uses **three transport mechanisms**, each for a different job:

| Mechanism | Used for | Why |
|---|---|---|
| HTTP | Login, register, plugin reports, capability invocation | Reliable request/response; errors are visible in the response |
| WebSocket | Install, uninstall, capability dispatch | Server → device push; low latency; keeps the device state visible to the backend |
| 60 s polling (`_sync_once`) | Reconciliation | Catches backend changes made while the WS was down; survives backend restarts |

The reconciliation loop is the correctness anchor. The WS is the fast path. `pai-device enable` writes intent to the DB; `_sync_once` acts on it within 60 s. `pai-device install` pushes directly over WS; `_sync_once` corrects any drift if the push was missed.

### Why `device_id` is persisted

Without persistence, every restart mints a new `device_id` and the backend accumulates dead device rows. With it, `POST /devices/register` with an existing id updates the row in place — the backend's `register_device()` has a "restore into memory with the caller's id" branch that re-creates the `DeviceInfo` from the DB row.

If the id is deleted, the next registration mints a new device — which is intentional for re-associating a device with a different user (deleting the file effectively un-enrolls this device).

### Why plugins are not started at boot

`load_existing()` registers every on-disk plugin but doesn't call `start()`. Startup policy lives in the backend's `device_plugins.enabled_on_device` flag.

If the agent started everything on disk unconditionally, a plugin the user just disabled would start briefly (2 s of runtime, then `_sync_once` would stop it). That's a flap. Deferring to `_sync_once` means:

1. Boot → `load_existing` registers plugins (fast)
2. Boot → register device, open WS
3. +2 s → `_sync_once` reads backend state, starts only what's enabled

The tradeoff is ~2 s of unavailability per restart for each enabled plugin. Acceptable, and only incurred on restart.

### Security model

The agent executes arbitrary code from `~/.pai/plugins/`. The only protection is:

1. The sha256 verify after download (ensures the file matches what the backend's index advertised)
2. Trust in the backend (only the backend can serve the file)

There is **no sandbox** — a malicious plugin runs with the agent's full privileges, can read any file the agent's user can read, can open network connections, can `os._exit()`. For a single-tenant deployment where you write the plugins, this is acceptable. For multi-tenant, you'd need:

- **Level 1** — subprocess + `RLIMIT_*` + JSON-RPC over stdio (isolates crashes and resource use; doesn't restrict filesystem or network)
- **Level 2** — container with `--read-only --user nobody --network none` + seccomp (real isolation for broadly trusted code)
- **Level 3** — gVisor (`runsc`) or Firecracker microVMs (multi-tenant, untrusted authors)

Not implemented today. If you need it, the JSON-RPC boundary at Level 1 is the natural first step — the adapter's interface (`start` / `invoke` / `stop`) already maps cleanly to a subprocess protocol.

### What this module does not do

- **Author plugins.** Plugins are authored separately and published to the backend's `plugin_registry/`.
- **Manage users or permissions.** Everything about who can do what lives in the backend's `auth` routes and `device_plugins` ownership column.
- **Provide plugin sandboxing.** See [Security model](#security-model).
- **Persist logs to a remote aggregator.** If you need Loki / ELK / Datadog, add a loguru sink that ships `logs/app.log` or `logs/http_access.log` — the format is already line-oriented JSON if you set `json_access=True` in the backend's logging config.

---

## See also

- **Backend README** — routes, database, plugin manager internals (`../README.md`)
- **Plugin authoring guide** — contract, manifest schema, examples (`plugin_registry/README.md`)
- **Debugging guide** — common errors and their fixes (`docs/debugging.md`)